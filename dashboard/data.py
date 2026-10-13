"""Data access for the dashboard. Read-only except for uploads to raw/<dataset>/.

Plain functions take boto3 clients so they can be tested with moto; the `load_*` wrappers at
the bottom add Streamlit caching (ttl=30 s) and are what the pages call.

Access rules (enforced by the DashboardReadPolicy IAM policy, not just by this code):
  - jobs come from Query on the status-index GSI, one query per status (no table scans)
  - reports/processed/rejected objects are only read, never written
  - the only write is PutObject to raw/<dataset>/, which starts a normal pipeline job
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import pandas as pd
import streamlit as st
from boto3.dynamodb.conditions import Key
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

STATUSES = ("PROCESSING", "SUCCEEDED", "FAILED")
DATASET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # keep test files small while on the Free plan
JOB_COLUMNS = [
    "job_id", "status", "dataset", "source_key", "started_at", "finished_at", "duration_ms",
    "attempts", "rows_total", "rows_accepted", "rows_rejected", "acceptance_rate",
    "error_type", "error_message", "report_key", "ingest_date", "rejection_reasons", "metrics",
    "outputs",
]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DashboardConfig:
    bucket: str
    table: str
    region: str
    queue_url: str | None = None
    dlq_url: str | None = None
    log_group: str | None = None
    athena_workgroup: str | None = None
    glue_database: str | None = None
    profile: str | None = None
    display_tz: str = "Asia/Kolkata"

    @classmethod
    def from_env(cls) -> DashboardConfig:
        missing = [name for name in ("BUCKET_NAME", "TABLE_NAME") if not os.environ.get(name)]
        if missing:
            raise ConfigError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            bucket=os.environ["BUCKET_NAME"],
            table=os.environ["TABLE_NAME"],
            region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            or "ap-south-1",
            queue_url=os.environ.get("QUEUE_URL") or None,
            dlq_url=os.environ.get("DLQ_URL") or None,
            log_group=os.environ.get("LOG_GROUP") or None,
            athena_workgroup=os.environ.get("ATHENA_WORKGROUP") or None,
            glue_database=os.environ.get("GLUE_DATABASE") or None,
            profile=os.environ.get("AWS_PROFILE") or None,
            display_tz=os.environ.get("DISPLAY_TZ", "Asia/Kolkata"),
        )


# ----------------------------------------------------------------------------- conversions

def plain(value: Any) -> Any:
    """DynamoDB Decimals -> int/float, recursively."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def split_s3_uri(uri: str) -> tuple[str, str]:
    without = uri.removeprefix("s3://")
    bucket, _, key = without.partition("/")
    return bucket, key


# ----------------------------------------------------------------------------- jobs

def query_jobs(table, statuses=STATUSES, limit_per_status: int = 500) -> list[dict]:
    """Newest jobs first for each status, via the status-index GSI (never a Scan)."""
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
    return [plain(i) for i in items]


def jobs_frame(items: list[dict], display_tz: str = "UTC") -> pd.DataFrame:
    df = pd.DataFrame(items)
    for column in JOB_COLUMNS:
        if column not in df:
            df[column] = None
    df = df[JOB_COLUMNS].copy()
    for column in ("started_at", "finished_at"):
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce").dt.tz_convert(display_tz)
    for column in ("duration_ms", "attempts", "rows_total", "rows_accepted", "rows_rejected",
                   "acceptance_rate"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ("rejection_reasons", "metrics", "outputs"):
        df[column] = df[column].apply(lambda v: v if isinstance(v, dict) else {})
    df["file"] = df["source_key"].fillna("").str.rsplit("/", n=1).str[-1]
    return df.sort_values("started_at", ascending=False, na_position="last").reset_index(drop=True)


def reason_counts(jobs: pd.DataFrame) -> pd.DataFrame:
    """One row per (job, reason) from the rejection_reasons maps stored on each job."""
    rows = [
        {"job_id": job.job_id, "file": job.file, "dataset": job.dataset,
         "started_at": job.started_at, "reason": reason, "count": count}
        for job in jobs.itertuples()
        for reason, count in (job.rejection_reasons or {}).items()
    ]
    return pd.DataFrame(rows, columns=["job_id", "file", "dataset", "started_at", "reason",
                                       "count"])


# ----------------------------------------------------------------------------- S3 objects

def get_report(s3, bucket: str, key: str) -> dict:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def get_csv_sample(s3, bucket: str, key: str, rows: int = 50,
                   max_bytes: int = 256 * 1024) -> pd.DataFrame:
    """First rows of a CSV via a ranged GET, so a big file is never downloaded whole."""
    body = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_bytes - 1}")["Body"].read()
    text = body.decode("utf-8", errors="replace")
    if len(body) == max_bytes:
        text = text.rsplit("\n", 1)[0]  # drop a partial last line
    parsed = list(csv.reader(io.StringIO(text)))
    if not parsed:
        return pd.DataFrame()
    header, data = parsed[0], parsed[1:rows + 1]
    return pd.DataFrame([r + [""] * (len(header) - len(r)) for r in data],
                        columns=header).iloc[:, :len(header)]


def combine_aggregates(reports: list[dict]) -> dict[str, Any]:
    """Merge report aggregates across jobs: daily lists summed per date, groups summed per key."""
    combined: dict[str, Any] = {}
    for report in reports:
        for name, value in (report.get("aggregates") or {}).items():
            if isinstance(value, list):
                bucket = combined.setdefault(name, {})
                for point in value:
                    total, count = bucket.get(point["date"], (0.0, 0))
                    bucket[point["date"]] = (total + point["total"], count + point["count"])
            elif isinstance(value, dict):
                bucket = combined.setdefault(name, {})
                for key, total in value.items():
                    bucket[key] = bucket.get(key, 0.0) + total
    out: dict[str, Any] = {}
    for name, bucket in combined.items():
        sample = next(iter(bucket.values()), None)
        if isinstance(sample, tuple):
            out[name] = pd.DataFrame(
                [{"date": d, "total": round(t, 2), "count": c}
                 for d, (t, c) in sorted(bucket.items())])
            out[name]["date"] = pd.to_datetime(out[name]["date"])
        else:
            out[name] = (pd.Series(bucket, name="total").round(2).sort_values(ascending=False)
                         .rename_axis("key").reset_index())
    return out


# ----------------------------------------------------------------------------- operations

def queue_depths(sqs, queue_url: str | None, dlq_url: str | None) -> dict[str, int | None]:
    attrs = ["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]
    out: dict[str, int | None] = {"waiting": None, "in_flight": None, "dlq": None}
    if queue_url:
        a = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=attrs)["Attributes"]
        out["waiting"] = int(a["ApproximateNumberOfMessages"])
        out["in_flight"] = int(a["ApproximateNumberOfMessagesNotVisible"])
    if dlq_url:
        a = sqs.get_queue_attributes(QueueUrl=dlq_url,
                                     AttributeNames=["ApproximateNumberOfMessages"])["Attributes"]
        out["dlq"] = int(a["ApproximateNumberOfMessages"])
    return out


def safe_filename(name: str) -> str:
    stem = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "upload.csv"
    return stem if stem.lower().endswith(".csv") else f"{stem}.csv"


def job_id_for(bucket: str, key: str, etag: str) -> str:
    """Must match etl_processor.app.make_job_id."""
    return hashlib.sha256(f"{bucket}/{key}/{etag.strip(chr(34))}".encode()).hexdigest()[:16]


def upload_csv(s3, bucket: str, dataset: str, filename: str, data: bytes) -> dict[str, str]:
    if not DATASET_NAME.match(dataset):
        raise ValueError("dataset must be lowercase letters, digits or _ (e.g. sales)")
    if not data:
        raise ValueError("the file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    key = f"raw/{dataset}/{safe_filename(filename)}"
    response = s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="text/csv")
    return {"key": key, "version_id": response.get("VersionId", ""),
            "job_id": job_id_for(bucket, key, response["ETag"])}


# ----------------------------------------------------------------------------- Athena

_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|grant|revoke|msck|unload|vacuum|optimize)\b",
    re.IGNORECASE)
_STRINGS = re.compile(r"'(?:[^']|'')*'")


def validate_select(sql: str) -> str:
    """Allow exactly one read-only statement (SELECT / WITH ... SELECT).

    A usability guard only: the real enforcement is IAM - the dashboard identity has no Glue
    write permissions and can write to S3 only under raw/ and athena-results/.
    """
    stripped = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("enter a query")
    code = _STRINGS.sub("''", stripped)  # keywords inside 'string literals' are fine
    if ";" in code:
        raise ValueError("only one statement is allowed")
    if not re.match(r"^(select|with)\b", code, re.IGNORECASE):
        raise ValueError("only SELECT queries are allowed")
    if _FORBIDDEN.search(code):
        raise ValueError("only read-only SELECT queries are allowed")
    return stripped


def run_athena(athena, workgroup: str, database: str, sql: str, timeout: float = 60,
               poll: float = 1.0, max_rows: int = 1000) -> tuple[pd.DataFrame, dict]:
    query_id = athena.start_query_execution(
        QueryString=validate_select(sql), WorkGroup=workgroup,
        QueryExecutionContext={"Database": database})["QueryExecutionId"]
    deadline = time.time() + timeout
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() > deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError(f"query {query_id} did not finish in {timeout:.0f}s")
        time.sleep(poll)
    if state != "SUCCEEDED":
        raise RuntimeError(execution["Status"].get("StateChangeReason", state))

    rows: list[list[str | None]] = []
    header: list[str] = []
    kwargs = {"QueryExecutionId": query_id}
    while len(rows) < max_rows:
        page = athena.get_query_results(**kwargs)
        result_rows = page["ResultSet"]["Rows"]
        if not header:
            header = [c["Name"] for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
            result_rows = result_rows[1:]  # first row of the first page repeats the header
        rows.extend([d.get("VarCharValue") for d in r["Data"]] for r in result_rows)
        if "NextToken" not in page:
            break
        kwargs["NextToken"] = page["NextToken"]
    df = pd.DataFrame(rows[:max_rows], columns=header)
    for column in df:
        converted = pd.to_numeric(df[column], errors="coerce")
        if converted.notna().sum() == df[column].notna().sum():
            df[column] = converted
    stats = execution.get("Statistics", {})
    return df, {"query_id": query_id,
                "bytes_scanned": stats.get("DataScannedInBytes", 0),
                "runtime_ms": stats.get("TotalExecutionTimeInMillis", 0)}


# ----------------------------------------------------------------------------- console links

def logs_console_url(region: str, log_group: str, job_id: str) -> str:
    group = quote(quote(log_group, safe=""), safe="").replace("%", "$")
    return (f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
            f"#logsV2:log-groups/log-group/{group}/log-events$3FfilterPattern$3D{job_id}")


def s3_console_url(region: str, uri: str) -> str:
    bucket, key = split_s3_uri(uri)
    return (f"https://{region}.console.aws.amazon.com/s3/object/{bucket}"
            f"?region={region}&prefix={quote(key)}")


# ----------------------------------------------------------------------------- cached wrappers

@st.cache_resource
def config() -> DashboardConfig:
    return DashboardConfig.from_env()


@st.cache_resource
def session() -> boto3.Session:
    cfg = config()
    if os.environ.get("AWS_PROFILE") == "":
        del os.environ["AWS_PROFILE"]  # boto3 would look for a profile literally named ""
    return boto3.Session(profile_name=cfg.profile, region_name=cfg.region)


def client(name: str):
    return session().client(name)


@st.cache_data(ttl=30, show_spinner="Loading jobs...")
def load_jobs() -> pd.DataFrame:
    cfg = config()
    table = session().resource("dynamodb").Table(cfg.table)
    return jobs_frame(query_jobs(table), cfg.display_tz)


@st.cache_data(ttl=300, show_spinner=False)
def load_report(key: str) -> dict:
    return get_report(client("s3"), config().bucket, key)


@st.cache_data(ttl=300, show_spinner="Loading reports...")
def load_reports(keys: tuple[str, ...]) -> list[dict]:
    s3, bucket = client("s3"), config().bucket
    reports = []
    for key in keys:
        try:
            reports.append(get_report(s3, bucket, key))
        except s3.exceptions.NoSuchKey:
            continue  # reports expire after 30 days (lifecycle rule)
    return reports


@st.cache_data(ttl=300, show_spinner=False)
def load_csv_sample(uri: str, rows: int = 50) -> pd.DataFrame:
    bucket, key = split_s3_uri(uri)
    return get_csv_sample(client("s3"), bucket, key, rows)


@st.cache_data(ttl=15, show_spinner=False)
def load_queue_depths() -> dict[str, int | None]:
    cfg = config()
    return queue_depths(client("sqs"), cfg.queue_url, cfg.dlq_url)


@st.cache_data(ttl=600, show_spinner="Running Athena query...")
def load_athena(sql: str) -> tuple[pd.DataFrame, dict]:
    cfg = config()
    return run_athena(client("athena"), cfg.athena_workgroup, cfg.glue_database, sql)


def refresh() -> None:
    load_jobs.clear()
    load_queue_depths.clear()
