"""DynamoDB job repository and the idempotency guard.

A job is claimed with ONE conditional write that succeeds only if the job is new, previously
FAILED, or PROCESSING with an expired lease. Everything else is either a duplicate delivery
(already SUCCEEDED) or another attempt that still holds a live lease.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from botocore.exceptions import ClientError

PROCESSING = "PROCESSING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
DEFAULT_LEASE_SECONDS = 300  # must stay below the SQS visibility timeout (360 s)
MAX_ERROR_LENGTH = 1000


class ClaimStatus(StrEnum):
    CLAIMED = "CLAIMED"
    ALREADY_SUCCEEDED = "ALREADY_SUCCEEDED"
    IN_PROGRESS_ELSEWHERE = "IN_PROGRESS_ELSEWHERE"


@dataclass(frozen=True)
class Claim:
    status: ClaimStatus
    item: dict[str, Any]

    @property
    def attempts(self) -> int:
        return int(self.item.get("attempts", 0))

    @property
    def ingest_date(self) -> str | None:
        return self.item.get("ingest_date")


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_dynamo(value: Any) -> Any:
    """DynamoDB rejects floats; convert them (recursively) to Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_dynamo(v) for v in value]
    return value


def _is_condition_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class JobRepository:
    def __init__(self, table, clock=time.time, lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self.table = table
        self.clock = clock
        self.lease_seconds = lease_seconds

    def create_or_claim(self, job_id: str, *, attempt_id: str, dataset: str, source_key: str,
                        source_etag: str, source_version: str | None = None) -> Claim:
        now = self.clock()
        values = {
            ":processing": PROCESSING,
            ":failed": FAILED,
            ":now": int(now),
            ":lease": int(now) + self.lease_seconds,
            ":started": iso(now),
            ":today": iso(now)[:10],
            ":attempt": attempt_id,
            ":dataset": dataset,
            ":key": source_key,
            ":etag": source_etag,
            ":one": 1,
        }
        sets = [
            "#status = :processing", "#lease = :lease", "#started = :started",
            "#attempt_id = :attempt", "#dataset = :dataset", "#source_key = :key",
            "#source_etag = :etag",
            # ingest_date is fixed at first claim so retries write to the same partition.
            "#ingest_date = if_not_exists(#ingest_date, :today)",
            "#first_seen = if_not_exists(#first_seen, :started)",
        ]
        names = {
            "#job_id": "job_id", "#status": "status", "#lease": "lease_expires_at",
            "#started": "started_at", "#attempt_id": "attempt_id", "#dataset": "dataset",
            "#source_key": "source_key", "#source_etag": "source_etag",
            "#ingest_date": "ingest_date", "#first_seen": "first_seen_at",
            "#attempts": "attempts", "#error_type": "error_type",
            "#error_message": "error_message", "#finished": "finished_at",
        }
        if source_version:
            sets.append("#source_version = :version")
            names["#source_version"] = "source_version"
            values[":version"] = source_version
        try:
            response = self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=("SET " + ", ".join(sets) + " ADD #attempts :one"
                                  " REMOVE #error_type, #error_message, #finished"),
                ConditionExpression=("attribute_not_exists(#job_id) OR #status = :failed"
                                     " OR (#status = :processing AND #lease < :now)"),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
            return Claim(ClaimStatus.CLAIMED, response["Attributes"])
        except ClientError as exc:
            if not _is_condition_failure(exc):
                raise
        existing = self.get(job_id) or {}
        if existing.get("status") == SUCCEEDED:
            return Claim(ClaimStatus.ALREADY_SUCCEEDED, existing)
        # Live lease held by another attempt, or the item changed between the write and the
        # read: either way, let SQS redeliver later.
        return Claim(ClaimStatus.IN_PROGRESS_ELSEWHERE, existing)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self.table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")

    def _finish(self, job_id: str, attempt_id: str, status: str, fields: dict[str, Any]) -> bool:
        """Write the final state only if this attempt still owns the job.

        The condition stops a late or duplicate attempt from overwriting a newer result.
        Returns False if ownership was lost.
        """
        names = {"#status": "status", "#attempt_id": "attempt_id", "#lease": "lease_expires_at"}
        values = {":new_status": status, ":processing": PROCESSING, ":attempt": attempt_id}
        sets = ["#status = :new_status"]
        for i, (name, value) in enumerate(fields.items()):
            names[f"#f{i}"] = name
            values[f":f{i}"] = to_dynamo(value)
            sets.append(f"#f{i} = :f{i}")
        try:
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET " + ", ".join(sets) + " REMOVE #lease",
                ConditionExpression="#status = :processing AND #attempt_id = :attempt",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if _is_condition_failure(exc):
                return False
            raise

    def mark_succeeded(self, job_id: str, attempt_id: str, *, duration_ms: int,
                       report: dict[str, Any], report_key: str) -> bool:
        counts = report["counts"]
        return self._finish(job_id, attempt_id, SUCCEEDED, {
            "finished_at": iso(self.clock()),
            "duration_ms": duration_ms,
            "rows_total": counts["total"],
            "rows_accepted": counts["accepted"],
            "rows_rejected": counts["rejected"],
            "acceptance_rate": report["acceptance_rate"],
            "rejection_reasons": report["rejection_reasons"],
            "metrics": report["metrics"],
            "outputs": report["outputs"],
            "report_key": report_key,
        })

    def mark_failed(self, job_id: str, attempt_id: str, *, error_type: str, error_message: str,
                    duration_ms: int) -> bool:
        return self._finish(job_id, attempt_id, FAILED, {
            "finished_at": iso(self.clock()),
            "duration_ms": duration_ms,
            "error_type": error_type,
            "error_message": error_message[:MAX_ERROR_LENGTH],
        })

    def release(self, job_id: str, attempt_id: str, *, error_type: str,
                error_message: str) -> bool:
        """Give up the lease after a retryable error so the next delivery can claim at once,
        recording the last error for the dashboard. Status stays PROCESSING."""
        try:
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #lease = :zero, #error_type = :type, #error_message = :msg",
                ConditionExpression="#status = :processing AND #attempt_id = :attempt",
                ExpressionAttributeNames={"#lease": "lease_expires_at", "#status": "status",
                                          "#attempt_id": "attempt_id",
                                          "#error_type": "error_type",
                                          "#error_message": "error_message"},
                ExpressionAttributeValues={":zero": 0, ":processing": PROCESSING,
                                           ":attempt": attempt_id, ":type": error_type,
                                           ":msg": error_message[:MAX_ERROR_LENGTH]},
            )
            return True
        except ClientError as exc:
            if _is_condition_failure(exc):
                return False
            raise
