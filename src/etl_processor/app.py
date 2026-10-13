"""Lambda handler: SQS record -> S3 event -> one ETL job.

The handler only orchestrates. Validation, transformation and reporting live in pure modules;
S3, DynamoDB and metrics calls live in storage.py, jobs.py and metrics.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import boto3

from . import metrics
from .config import SchemaError, Settings, UnknownDatasetError, load_dataset_schema
from .jobs import ClaimStatus, JobRepository, iso
from .pipeline import process_rows
from .storage import JobOutputs, S3Storage, UnreadableFileError, output_keys, s3_uri
from .validator import HeaderValidationError

# Retrying these will never succeed: the file (or its dataset) is the problem.
POISON_ERRORS = (HeaderValidationError, UnknownDatasetError, SchemaError, UnreadableFileError)


class Outcome:
    DONE = "done"    # delete the message
    RETRY = "retry"  # report as a batch item failure so SQS redelivers it


class InvalidMessageError(ValueError):
    """The SQS body is not an S3 event notification we understand."""


_clients: dict[str, Any] = {}


def _aws(name: str):
    if name not in _clients:
        if name == "dynamodb":
            _clients[name] = boto3.resource("dynamodb")
        else:
            _clients[name] = boto3.client(name)
    return _clients[name]


def log(event: str, **fields: Any) -> None:
    line = {"timestamp": iso(time.time()), "event": event, **fields}
    print(json.dumps(line, default=str), flush=True)


def make_job_id(bucket: str, key: str, etag: str) -> str:
    return hashlib.sha256(f"{bucket}/{key}/{etag}".encode()).hexdigest()[:16]


def dataset_from_key(key: str) -> str:
    """raw/<dataset>/<anything>.csv -> <dataset>."""
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "raw" or not parts[1]:
        raise UnknownDatasetError(f"key {key!r} is not under raw/<dataset>/")
    return parts[1]


def parse_s3_records(body: str) -> list[dict[str, Any]]:
    """Return the ObjectCreated records in an SQS body; [] for S3's one-off s3:TestEvent."""
    try:
        message = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidMessageError("SQS body is not JSON") from exc
    if message.get("Event") == "s3:TestEvent":
        return []
    records = message.get("Records")
    if not isinstance(records, list):
        raise InvalidMessageError("SQS body has no Records list")
    parsed = []
    for record in records:
        if not str(record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        obj = record["s3"]["object"]
        parsed.append({
            "bucket": record["s3"]["bucket"]["name"],
            "key": unquote_plus(obj["key"]),
            "etag": obj.get("eTag", ""),
            "version_id": obj.get("versionId"),
            "size": obj.get("size"),
        })
    return parsed


class EtlJob:
    """Runs one S3 object through the pipeline. Collaborators are injected for testing."""

    def __init__(self, settings: Settings, storage: S3Storage, jobs: JobRepository,
                 max_receives: int = 3):
        self.settings = settings
        self.storage = storage
        self.jobs = jobs
        self.max_receives = max_receives

    def run(self, obj: dict[str, Any], receive_count: int, attempt_id: str) -> str:
        bucket, key = obj["bucket"], obj["key"]
        job_id = make_job_id(bucket, key, obj["etag"])
        try:
            dataset = dataset_from_key(key)
        except UnknownDatasetError:
            dataset = "unknown"

        claim = self.jobs.create_or_claim(
            job_id, attempt_id=attempt_id, dataset=dataset, source_key=key,
            source_etag=obj["etag"], source_version=obj.get("version_id"))
        if claim.status is ClaimStatus.ALREADY_SUCCEEDED:
            log("duplicate_delivery", job_id=job_id, source_key=key,
                receive_count=receive_count)
            return Outcome.DONE
        if claim.status is ClaimStatus.IN_PROGRESS_ELSEWHERE:
            log("in_progress_elsewhere", job_id=job_id, source_key=key)
            return Outcome.RETRY

        log("job_claimed", job_id=job_id, dataset=dataset, source_key=key,
            attempt=claim.attempts, receive_count=receive_count)
        started = time.monotonic()
        metric_dataset = metrics.UNKNOWN_DATASET
        try:
            schema = load_dataset_schema(dataset, self.settings.schema_dir)
            metric_dataset = schema.dataset
            report = self._execute(job_id, key, obj.get("version_id"), schema, claim, started)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            poison = isinstance(exc, POISON_ERRORS)
            final = poison or receive_count >= self.max_receives
            error = {"error_type": type(exc).__name__, "error_message": str(exc)}
            if final:
                self.jobs.mark_failed(job_id, attempt_id, duration_ms=duration_ms, **error)
            else:
                self.jobs.release(job_id, attempt_id, **error)
            log("job_failed" if final else "job_attempt_failed", level="ERROR",
                job_id=job_id, status="FAILED" if final else "RETRYING", poison=poison,
                receive_count=receive_count, duration_ms=duration_ms, **error)
            if receive_count >= self.max_receives:
                # Last delivery before the DLQ: count the job as failed exactly once.
                metrics.job_failed(metric_dataset, job_id=job_id, duration_ms=duration_ms)
            raise

        duration_ms = report["duration_ms"]
        owned = self.jobs.mark_succeeded(job_id, attempt_id, duration_ms=duration_ms,
                                         report=report, report_key=report["report_key"])
        if not owned:
            log("lease_lost", level="WARNING", job_id=job_id)
            return Outcome.DONE
        counts = report["counts"]
        metrics.job_succeeded(schema.dataset, job_id=job_id, accepted=counts["accepted"],
                              rejected=counts["rejected"], duration_ms=duration_ms)
        log("job_finished", job_id=job_id, status="SUCCEEDED", dataset=schema.dataset,
            rows_total=counts["total"], rows_accepted=counts["accepted"],
            rows_rejected=counts["rejected"], duration_ms=duration_ms, attempt=claim.attempts)
        return Outcome.DONE

    def _execute(self, job_id, key, version_id, schema, claim, started):
        ingest_date = claim.ingest_date or datetime.now(UTC).strftime("%Y-%m-%d")
        keys = output_keys(schema.dataset, job_id, ingest_date)
        bucket = self.storage.bucket

        with tempfile.TemporaryDirectory(prefix=f"job-{job_id}-") as tmp:
            outputs = JobOutputs(schema, Path(tmp))
            try:
                log("stage", stage="transform", job_id=job_id)
                report_builder = process_rows(
                    self.storage.iter_csv(key, version_id), schema,
                    accept=outputs.write_accepted, reject=outputs.write_rejected,
                    on_header=outputs.on_header)
            finally:
                outputs.close()

            log("stage", stage="write_outputs", job_id=job_id)
            uris: dict[str, str] = {}
            fmt = self.settings.output_format
            if fmt in ("csv", "both"):
                uris["processed"] = self.storage.upload_file(
                    outputs.processed_path, keys.processed, "text/csv")
            if fmt in ("parquet", "both"):
                uris["curated"] = self.storage.upload_file(
                    outputs.write_parquet(), keys.curated, "application/vnd.apache.parquet")
            uris["rejected"] = self.storage.upload_file(
                outputs.rejected_path, keys.rejected, "text/csv")

        duration_ms = int((time.monotonic() - started) * 1000)
        report = report_builder.build(
            job_id=job_id,
            status="SUCCEEDED",
            source=s3_uri(bucket, key),
            source_version=version_id,
            ingest_date=ingest_date,
            attempt=claim.attempts,
            started_at=claim.item.get("started_at"),
            finished_at=iso(time.time()),
            duration_ms=duration_ms,
            outputs=uris,
        )
        log("stage", stage="write_report", job_id=job_id)
        self.storage.put_json(keys.report, report)
        report["report_key"] = keys.report
        return report


def _build_job() -> EtlJob:
    settings = Settings.from_env()
    return EtlJob(
        settings=settings,
        storage=S3Storage(_aws("s3"), settings.bucket_name),
        jobs=JobRepository(_aws("dynamodb").Table(settings.table_name)),
        max_receives=int(os.environ.get("MAX_RECEIVE_COUNT", "3")),
    )


def handle_event(event: dict[str, Any], job: EtlJob, request_id: str) -> dict[str, Any]:
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId")
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            objects = parse_s3_records(record["body"])
            if not objects:
                log("ignored_message", message_id=message_id, reason="s3:TestEvent or no "
                    "ObjectCreated records")
            outcomes = [job.run(obj, receive_count, f"{request_id}:{message_id}")
                        for obj in objects]
        except Exception as exc:  # noqa: BLE001 - every failure must become a batch failure
            log("record_failed", level="ERROR", message_id=message_id,
                error_type=type(exc).__name__, error_message=str(exc))
            failures.append({"itemIdentifier": message_id})
            continue
        if Outcome.RETRY in outcomes:
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    return handle_event(event, _build_job(), request_id)
