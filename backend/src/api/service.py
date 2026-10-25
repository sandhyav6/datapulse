"""Read-only data access for the public web API. No writes anywhere.

Every function takes its AWS clients as arguments, so tests run them against moto. Results are
plain JSON-ready dicts and lists (DynamoDB Decimals converted, timestamps as ISO-8601 UTC
strings; the browser formats them in the viewer's time zone).
"""

from __future__ import annotations

import csv
import io
import json
import statistics
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from boto3.dynamodb.conditions import Key

STATUSES = ("PROCESSING", "SUCCEEDED", "FAILED")
SRC = Path(__file__).resolve().parent.parent
SCHEMA_DIR = SRC / "schemas"
SOURCES_FILE = SRC / "sources.json"
JOB_FIELDS = (
    "job_id", "status", "dataset", "source_key", "started_at", "finished_at", "duration_ms",
    "attempts", "rows_total", "rows_accepted", "rows_rejected", "acceptance_rate", "error_type",
    "error_message", "rejection_reasons", "metrics",
)
TOP_GROUPS = 15

# The only SQL the public site can run: fixed, read-only, against the Parquet table.
ATHENA_QUERIES = {
    "monthly-revenue": {
        "title": "Monthly revenue",
        "sql": "SELECT date_format(transaction_date, '%Y-%m') AS month, "
               "SUM(transaction_amount) AS revenue, COUNT(*) AS transactions "
               "FROM sales GROUP BY 1 ORDER BY 1",
    },
    "top-categories": {
        "title": "Top 10 categories",
        "sql": "SELECT product_category, SUM(transaction_amount) AS revenue, "
               "SUM(quantity) AS units FROM sales GROUP BY 1 ORDER BY revenue DESC LIMIT 10",
    },
    "region-by-month": {
        "title": "Revenue by region and month",
        "sql": "SELECT region, date_format(transaction_date, '%Y-%m') AS month, "
               "SUM(transaction_amount) AS revenue FROM sales GROUP BY 1, 2 ORDER BY 2, 3 DESC",
    },
}


class NotFound(LookupError):
    pass


# ----------------------------------------------------------------------------- helpers

def plain(value: Any) -> Any:
    """DynamoDB Decimals -> int/float, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def file_name(job: dict) -> str:
    return (job.get("source_key") or "").rsplit("/", 1)[-1]


def summarise(job: dict) -> dict:
    out = {k: job.get(k) for k in JOB_FIELDS if k in job}
    out["file"] = file_name(job)
    return out


def schema_info(dataset: str) -> dict:
    path = SCHEMA_DIR / f"{dataset}.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {"description": raw.get("description", ""),
            "load_type": raw.get("load_type", "incremental")}


def split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key = uri.removeprefix("s3://").partition("/")
    return bucket, key


# ----------------------------------------------------------------------------- jobs

def query_jobs(table, statuses=STATUSES, limit_per_status: int = 500) -> list[dict]:
    """Newest jobs first per status via the status-index GSI (never a Scan)."""
    items: list[dict] = []
    for status in statuses:
        kwargs = {"IndexName": "status-index", "KeyConditionExpression": Key("status").eq(status),
                  "ScanIndexForward": False}
        fetched = 0
        while fetched < limit_per_status:
            page = table.query(**kwargs, Limit=min(100, limit_per_status - fetched))
            items.extend(page["Items"])
            fetched += len(page["Items"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    jobs = [plain(i) for i in items]
    jobs.sort(key=lambda j: j.get("started_at") or "", reverse=True)
    return jobs


def list_jobs(jobs: list[dict], status: str | None = None, dataset: str | None = None,
              limit: int = 200) -> list[dict]:
    selected = [j for j in jobs
                if (not status or j.get("status") == status)
                and (not dataset or j.get("dataset") == dataset)]
    return [summarise(j) for j in selected[:limit]]


def overview(jobs: list[dict]) -> dict:
    done = [j for j in jobs if j.get("status") == "SUCCEEDED"]
    rows_total = sum(j.get("rows_total", 0) for j in done)
    rows_accepted = sum(j.get("rows_accepted", 0) for j in done)
    durations = sorted(j["duration_ms"] for j in done if "duration_ms" in j)
    per_day: dict[str, Counter] = defaultdict(Counter)
    rows_day: dict[str, Counter] = defaultdict(Counter)
    for job in jobs:
        day = (job.get("started_at") or "")[:10]
        if not day:
            continue
        per_day[day][job.get("status", "UNKNOWN")] += 1
        if job.get("status") == "SUCCEEDED":
            rows_day[day]["accepted"] += job.get("rows_accepted", 0)
            rows_day[day]["rejected"] += job.get("rows_rejected", 0)
    return {
        "kpis": {
            "files_processed": len(done),
            "failed_jobs": sum(j.get("status") == "FAILED" for j in jobs),
            "processing_jobs": sum(j.get("status") == "PROCESSING" for j in jobs),
            "rows_processed": rows_total,
            "acceptance_rate": round(100 * rows_accepted / rows_total, 2) if rows_total else None,
            "avg_duration_ms": round(statistics.fmean(durations)) if durations else None,
            "p95_duration_ms": (durations[min(len(durations) - 1,
                                              int(round(0.95 * (len(durations) - 1))))]
                                if durations else None),
            "datasets": len({j.get("dataset") for j in done}),
        },
        "jobs_per_day": [{"day": d, **{s: c.get(s, 0) for s in STATUSES}}
                         for d, c in sorted(per_day.items())],
        "rows_per_day": [{"day": d, "accepted": c["accepted"], "rejected": c["rejected"]}
                         for d, c in sorted(rows_day.items())],
        "latest": [summarise(j) for j in jobs[:10]],
    }


def get_job(table, job_id: str) -> dict:
    item = table.get_item(Key={"job_id": job_id}).get("Item")
    if not item:
        raise NotFound(f"job {job_id} not found")
    return plain(item)


# ----------------------------------------------------------------------------- S3 objects

def get_json(s3, bucket: str, key: str) -> dict | None:
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None  # reports expire after 30 days (lifecycle rule)


def job_detail(table, s3, bucket: str, job_id: str) -> dict:
    job = get_job(table, job_id)
    report = get_json(s3, bucket, job["report_key"]) if job.get("report_key") else None
    out = summarise(job)
    out["outputs"] = job.get("outputs", {})
    out["ingest_date"] = job.get("ingest_date")
    return {"job": out, "report": report}


def csv_sample(s3, bucket: str, key: str, rows: int = 50,
               max_bytes: int = 256 * 1024) -> dict:
    """First rows of a CSV via a ranged GET, so a large file is never downloaded whole."""
    body = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")["Body"].read()
    text = body.decode("utf-8", errors="replace")
    if len(body) == max_bytes:
        text = text.rsplit("\n", 1)[0]
    parsed = list(csv.reader(io.StringIO(text)))
    if not parsed:
        return {"columns": [], "rows": []}
    header = parsed[0]
    return {"columns": header,
            "rows": [r + [""] * (len(header) - len(r)) for r in parsed[1:rows + 1]]}


def rejected_sample(table, s3, job_id: str, rows: int = 50) -> dict:
    job = get_job(table, job_id)
    uri = (job.get("outputs") or {}).get("rejected")
    if not uri or not job.get("rows_rejected"):
        return {"columns": [], "rows": []}
    bucket, key = split_s3_uri(uri)
    return csv_sample(s3, bucket, key, rows)


# ----------------------------------------------------------------------------- datasets

def latest_by_dataset(jobs: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for job in jobs:  # newest first
        latest.setdefault(job.get("dataset") or "unknown", job)
    return latest


def datasets(jobs: list[dict]) -> list[dict]:
    names = {p.stem for p in SCHEMA_DIR.glob("*.json")} | {
        j["dataset"] for j in jobs if j.get("dataset") and j["dataset"] != "unknown"}
    latest = latest_by_dataset(jobs)
    out = []
    for name in sorted(names):
        job = latest.get(name)
        out.append({
            "name": name, **schema_info(name),
            "jobs": sum(j.get("dataset") == name for j in jobs),
            "succeeded": sum(j.get("dataset") == name and j.get("status") == "SUCCEEDED"
                             for j in jobs),
            "latest": summarise(job) if job else None,
        })
    return out


def _chart_points(kind: str, value: Any) -> tuple[list[dict], int]:
    if kind == "daily":
        return list(value), len(value)
    if kind == "yearly":
        points = sorted(({"year": int(k), "value": v} for k, v in value.items()),
                        key=lambda p: p["year"])
        return points, len(points)
    points = sorted(({"key": k, "value": v} for k, v in value.items()),
                    key=lambda p: (-p["value"], p["key"]))
    return points[:TOP_GROUPS], len(points)


def _combine(reports: list[dict]) -> dict[str, Any]:
    """Incremental datasets: sum every report's (additive) aggregates."""
    combined: dict[str, Any] = {}
    for report in reports:
        for name, value in (report.get("aggregates") or {}).items():
            if isinstance(value, list):
                bucket = combined.setdefault(name, {})
                for p in value:
                    total, count = bucket.get(p["date"], (0.0, 0))
                    bucket[p["date"]] = (total + p["total"], count + p["count"])
            else:
                bucket = combined.setdefault(name, {})
                for key, v in value.items():
                    bucket[key] = bucket.get(key, 0) + v
    out: dict[str, Any] = {}
    for name, bucket in combined.items():
        sample = next(iter(bucket.values()), None)
        if isinstance(sample, tuple):
            out[name] = [{"date": d, "total": round(t, 2), "count": c}
                         for d, (t, c) in sorted(bucket.items())]
        else:
            out[name] = {k: round(v, 4) if isinstance(v, float) else v for k, v in bucket.items()}
    return out


def analytics(jobs: list[dict], s3, bucket: str, dataset: str, max_reports: int = 200) -> dict:
    done = [j for j in jobs if j.get("dataset") == dataset and j.get("status") == "SUCCEEDED"
            and j.get("report_key")]
    if not done:
        raise NotFound(f"no successful jobs for dataset {dataset!r}")
    latest = get_json(s3, bucket, done[0]["report_key"])
    if latest is None:
        raise NotFound("the latest report has expired (30-day lifecycle rule)")
    specs = latest.get("aggregate_specs", {})
    snapshot = latest.get("load_type") == "snapshot"
    if snapshot:
        aggregates, reports = latest.get("aggregates", {}), [latest]
        metrics = [{"name": k, "value": v, "func": latest.get("metric_specs", {}).get(k)}
                   for k, v in (latest.get("metrics") or {}).items()]
    else:
        reports = [r for r in (get_json(s3, bucket, j["report_key"])
                               for j in done[:max_reports]) if r]
        aggregates = _combine(reports)
        rows = sum(r["counts"]["accepted"] for r in reports)
        daily = next((v for v in aggregates.values() if isinstance(v, list)), None)
        total = round(sum(p["total"] for p in daily), 2) if daily else None
        metrics = [{"name": "files", "value": len(reports), "func": "count"},
                   {"name": "clean_rows", "value": rows, "func": "count"}]
        if total is not None:
            metrics += [{"name": "total_value", "value": total, "func": "sum"},
                        {"name": "average_per_row",
                         "value": round(total / rows, 2) if rows else None, "func": "mean"}]
    charts = []
    for name, value in aggregates.items():
        spec = specs.get(name) or {"type": "daily" if isinstance(value, list) else "group"}
        points, size = _chart_points(spec.get("type", "group"), value)
        charts.append({"name": name, "type": spec.get("type", "group"),
                       "func": spec.get("func", "sum"), "where": spec.get("where") or {},
                       "exclude": spec.get("exclude") or {}, "points": points, "size": size})
    return {
        "dataset": dataset, **schema_info(dataset),
        "mode": "snapshot" if snapshot else "incremental",
        "measure": latest.get("measure"),
        "source": {"job_id": done[0]["job_id"], "started_at": done[0].get("started_at"),
                   "files": len(reports), "rows": latest["counts"]["accepted"]},
        "metrics": metrics,
        "charts": charts,
    }


def quality(jobs: list[dict], dataset: str | None = None) -> dict:
    done = [j for j in jobs if j.get("status") == "SUCCEEDED"
            and (not dataset or j.get("dataset") == dataset)]
    totals: Counter = Counter()
    for job in done:
        totals.update(job.get("rejection_reasons") or {})
    rows_total = sum(j.get("rows_total", 0) for j in done)
    rows_accepted = sum(j.get("rows_accepted", 0) for j in done)
    return {
        "dataset": dataset,
        "rows_checked": rows_total,
        "rows_rejected": rows_total - rows_accepted,
        "acceptance_rate": round(100 * rows_accepted / rows_total, 2) if rows_total else None,
        "reasons": [{"reason": r, "count": c} for r, c in totals.most_common()],
        "per_job": [{"job_id": j["job_id"], "file": file_name(j), "dataset": j.get("dataset"),
                     "started_at": j.get("started_at"), "rows_total": j.get("rows_total"),
                     "rows_rejected": j.get("rows_rejected"),
                     "acceptance_rate": j.get("acceptance_rate"),
                     "reasons": j.get("rejection_reasons") or {}}
                    for j in sorted(done, key=lambda j: j.get("started_at") or "")],
    }


# ----------------------------------------------------------------------------- sources & ops

def sources(jobs: list[dict], schedule: str) -> dict:
    raw = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]
    latest = latest_by_dataset(jobs)
    return {"schedule": schedule, "sources": [
        {**{k: s.get(k) for k in ("dataset", "url", "homepage", "publisher", "license")},
         **schema_info(s["dataset"]),
         "raw_key": f"raw/{s['dataset']}/{s['dataset']}.csv",
         "latest": summarise(latest[s["dataset"]]) if s["dataset"] in latest else None}
        for s in raw]}


def queue_depths(sqs, queue_url: str | None, dlq_url: str | None) -> dict:
    out: dict[str, int | None] = {"waiting": None, "in_flight": None, "dlq": None}
    if queue_url:
        a = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        out["waiting"] = int(a["ApproximateNumberOfMessages"])
        out["in_flight"] = int(a["ApproximateNumberOfMessagesNotVisible"])
    if dlq_url:
        a = sqs.get_queue_attributes(QueueUrl=dlq_url,
                                     AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]
        out["dlq"] = int(a["ApproximateNumberOfMessages"])
    return out


def operations(jobs: list[dict], sqs, queue_url: str | None, dlq_url: str | None) -> dict:
    return {
        "queues": queue_depths(sqs, queue_url, dlq_url),
        "failed": [summarise(j) for j in jobs if j.get("status") == "FAILED"],
        "retrying": [summarise(j) for j in jobs
                     if j.get("status") == "PROCESSING" and j.get("error_type")],
    }


# ----------------------------------------------------------------------------- Athena

def run_athena(athena, workgroup: str, database: str, sql: str, timeout: float = 25,
               poll: float = 0.5, max_rows: int = 500) -> dict:
    query_id = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup,
        QueryExecutionContext={"Database": database})["QueryExecutionId"]
    deadline = time.time() + timeout
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() > deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError("query took too long")
        time.sleep(poll)
    if state != "SUCCEEDED":
        raise RuntimeError(execution["Status"].get("StateChangeReason", state))
    header: list[str] = []
    rows: list[list[str | None]] = []
    kwargs = {"QueryExecutionId": query_id}
    while len(rows) < max_rows:
        page = athena.get_query_results(**kwargs)
        result_rows = page["ResultSet"]["Rows"]
        if not header:
            header = [c["Name"] for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
            result_rows = result_rows[1:]
        rows.extend([d.get("VarCharValue") for d in r["Data"]] for r in result_rows)
        if "NextToken" not in page:
            break
        kwargs["NextToken"] = page["NextToken"]
    stats = execution.get("Statistics", {})
    return {"columns": header, "rows": rows[:max_rows],
            "bytes_scanned": stats.get("DataScannedInBytes", 0),
            "runtime_ms": stats.get("TotalExecutionTimeInMillis", 0)}
