"""CloudWatch custom metrics via Embedded Metric Format (EMF).

A metric is just a structured log line that CloudWatch Logs turns into a metric, so there are
no PutMetricData calls and no extra IAM permissions. Only the Dataset dimension is used, so
each dataset costs exactly four custom metrics.
"""

from __future__ import annotations

import json
import time
from typing import Any

NAMESPACE = "EtlPipeline"
UNKNOWN_DATASET = "unknown"


def emf_payload(dataset: str, metrics: dict[str, tuple[float, str]],
                properties: dict[str, Any] | None = None,
                timestamp_ms: int | None = None) -> dict[str, Any]:
    """metrics maps name -> (value, unit). Properties are searchable but are not dimensions."""
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [["Dataset"]],
                "Metrics": [{"Name": name, "Unit": unit} for name, (_, unit) in metrics.items()],
            }],
        },
        "Dataset": dataset,
    }
    payload.update({name: value for name, (value, _) in metrics.items()})
    payload.update(properties or {})
    return payload


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str), flush=True)


def job_succeeded(dataset: str, *, job_id: str, accepted: int, rejected: int,
                  duration_ms: int) -> dict[str, Any]:
    payload = emf_payload(dataset, {
        "RowsAccepted": (accepted, "Count"),
        "RowsRejected": (rejected, "Count"),
        "JobDurationMs": (duration_ms, "Milliseconds"),
        "JobsFailed": (0, "Count"),
    }, {"job_id": job_id, "event": "metrics"})
    emit(payload)
    return payload


def job_failed(dataset: str, *, job_id: str, duration_ms: int) -> dict[str, Any]:
    payload = emf_payload(dataset, {
        "JobsFailed": (1, "Count"),
        "JobDurationMs": (duration_ms, "Milliseconds"),
    }, {"job_id": job_id, "event": "metrics"})
    emit(payload)
    return payload
