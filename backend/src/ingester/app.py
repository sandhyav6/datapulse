"""Scheduled ingestion of public CSV datasets into s3://<bucket>/raw/<dataset>/.

Runs on an EventBridge Scheduler schedule (and on demand). For each source in sources.json it
downloads the CSV over HTTPS (size-capped), hashes it, and writes it to
raw/<dataset>/<dataset>.csv only if the content changed since the last ingestion. That write
is an ordinary S3 upload, so the existing S3 -> SQS -> etl-processor pipeline takes over.

Change detection uses the sha256 stored in the current object's metadata: an unchanged source
costs one GET to the publisher and one HEAD to S3, and creates no new job or object version.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

DEFAULT_SOURCES = Path(__file__).resolve().parent.parent / "sources.json"
DEFAULT_MAX_BYTES = 5_000_000
USER_AGENT = "serverless-etl-platform-ingester/1.0 (+https://github.com)"


class IngestError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    dataset: str
    url: str
    max_bytes: int = DEFAULT_MAX_BYTES
    homepage: str = ""
    publisher: str = ""
    license: str = ""

    @property
    def key(self) -> str:
        return f"raw/{self.dataset}/{self.dataset}.csv"


def load_sources(path: str | Path = DEFAULT_SOURCES) -> list[Source]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    sources = []
    for item in raw["sources"]:
        source = Source(dataset=item["dataset"], url=item["url"],
                        max_bytes=int(item.get("max_bytes", DEFAULT_MAX_BYTES)),
                        homepage=item.get("homepage", ""), publisher=item.get("publisher", ""),
                        license=item.get("license", ""))
        if urlparse(source.url).scheme != "https":
            raise ValueError(f"{source.dataset}: only https:// sources are allowed")
        sources.append(source)
    if len({s.dataset for s in sources}) != len(sources):
        raise ValueError("each dataset may appear only once in sources.json")
    return sources


def fetch(url: str, max_bytes: int, timeout: float = 30) -> bytes:
    """Download at most max_bytes over HTTPS; larger responses are refused, not truncated."""
    if urlparse(url).scheme != "https":
        raise IngestError(f"refusing non-https URL {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise IngestError(f"{url} is {int(declared):,} bytes, over the {max_bytes:,} limit")
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise IngestError(f"{url} is over the {max_bytes:,} byte limit")
    return body


def log(event: str, **fields: Any) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(json.dumps({"timestamp": stamp, "event": event, **fields}, default=str), flush=True)


def current_sha256(s3, bucket: str, key: str) -> str | None:
    """sha256 recorded on the current object, or None if there is no object yet.

    Without s3:ListBucket, S3 answers HEAD on a missing key with 403 instead of 404, so both
    mean "not ingested yet"; a real permission problem still surfaces on the PutObject.
    """
    try:
        return s3.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("sha256")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "403", "NoSuchKey", "NotFound"):
            return None
        raise


def ingest(source: Source, s3, bucket: str, fetcher=None) -> dict[str, Any]:
    started = time.monotonic()
    body = (fetcher or fetch)(source.url, source.max_bytes)
    if not body.strip():
        raise IngestError(f"{source.url} returned an empty body")
    digest = hashlib.sha256(body).hexdigest()
    result = {"dataset": source.dataset, "key": source.key, "bytes": len(body),
              "sha256": digest}
    if current_sha256(s3, bucket, source.key) == digest:
        return {**result, "status": "unchanged",
                "duration_ms": int((time.monotonic() - started) * 1000)}
    response = s3.put_object(
        Bucket=bucket, Key=source.key, Body=body, ContentType="text/csv",
        Metadata={"sha256": digest, "source-url": source.url,
                  "fetched-at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return {**result, "status": "uploaded", "version_id": response.get("VersionId"),
            "etag": response.get("ETag", "").strip('"'),
            "duration_ms": int((time.monotonic() - started) * 1000)}


def run(sources: list[Source], s3, bucket: str, only: list[str] | None = None,
        fetcher=None) -> dict[str, Any]:
    results = []
    for source in sources:
        if only and source.dataset not in only:
            continue
        try:
            result = ingest(source, s3, bucket, fetcher)
        except Exception as exc:  # noqa: BLE001 - one bad source must not stop the others
            result = {"dataset": source.dataset, "status": "error",
                      "error_type": type(exc).__name__, "error_message": str(exc)[:500]}
        log("dataset_ingest", **result)
        results.append(result)
    summary = {status: sum(r["status"] == status for r in results)
               for status in ("uploaded", "unchanged", "error")}
    log("ingest_run_finished", **summary)
    return {"results": results, "summary": summary}


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """event may be {} (schedule) or {"datasets": ["world_population"]} to ingest a subset."""
    sources = load_sources(os.environ.get("SOURCES_FILE") or DEFAULT_SOURCES)
    only = (event or {}).get("datasets")
    outcome = run(sources, boto3.client("s3"), os.environ["BUCKET_NAME"], only)
    if outcome["summary"]["error"]:
        # Fail the invocation so the Lambda Errors metric (and IngesterErrorsAlarm) fire.
        failed = [r["dataset"] for r in outcome["results"] if r["status"] == "error"]
        raise IngestError(f"ingestion failed for: {', '.join(failed)}")
    return outcome
