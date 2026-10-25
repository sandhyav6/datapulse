"""Public read-only HTTP API for the React dashboard (API Gateway HTTP API, payload v2).

Reached through the Amplify-hosted website at /api/* (a reverse-proxy rule), on the same origin as
the site, so the browser never needs CORS. Protection for DynamoDB's free 5 RCU comes from API
Gateway throttling, short in-memory caches in this Lambda, and Cache-Control headers that let
browsers reuse responses. Only GET is allowed; nothing here can write, upload or start jobs.
"""

from __future__ import annotations

import json
import os
import re
import time
import traceback
from collections.abc import Callable
from typing import Any

import boto3

from . import service

JOB_ID = re.compile(r"^[0-9a-f]{16}$")
DATASET = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
JOBS_TTL = 20          # seconds the job list is reused within a warm Lambda
REPORT_TTL = 300
ATHENA_TTL = 900
CACHE_SECONDS = 30     # browser cache for most endpoints

_clients: dict[str, Any] = {}
_cache: dict[str, tuple[float, Any]] = {}


class BadRequest(ValueError):
    pass


def aws(name: str):
    if name not in _clients:
        _clients[name] = (boto3.resource("dynamodb") if name == "dynamodb"
                          else boto3.client(name))
    return _clients[name]


def cached(key: str, ttl: float, compute: Callable[[], Any]) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = compute()
    _cache[key] = (time.monotonic(), value)
    return value


def env(name: str) -> str | None:
    return os.environ.get(name) or None


def table():
    return aws("dynamodb").Table(os.environ["TABLE_NAME"])


def jobs() -> list[dict]:
    return cached("jobs", JOBS_TTL, lambda: service.query_jobs(table()))


def response(status: int, body: Any, max_age: int = CACHE_SECONDS) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": f"public, max-age={max_age}" if status == 200 else "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(body, default=str),
    }


def param(query: dict, name: str, pattern: re.Pattern | None = None) -> str | None:
    value = (query or {}).get(name) or None
    if value is not None and pattern is not None and not pattern.match(value):
        raise BadRequest(f"invalid {name}")
    return value


# ----------------------------------------------------------------------------- routes

def r_health(_q, _m) -> Any:
    return {"ok": True, "stage": env("STAGE"), "analytics": bool(env("ATHENA_WORKGROUP"))}


def r_overview(_q, _m) -> Any:
    return service.overview(jobs())


def r_jobs(query, _m) -> Any:
    status = param(query, "status")
    if status and status not in service.STATUSES:
        raise BadRequest("invalid status")
    limit = int(param(query, "limit", re.compile(r"^\d{1,3}$")) or 200)
    return {"jobs": service.list_jobs(jobs(), status, param(query, "dataset", DATASET), limit)}


def r_job(_q, match) -> Any:
    job_id = match["job_id"]
    return cached(f"job:{job_id}", JOBS_TTL, lambda: service.job_detail(
        table(), aws("s3"), os.environ["BUCKET_NAME"], job_id))


def r_rejected(_q, match) -> Any:
    job_id = match["job_id"]
    return cached(f"rejected:{job_id}", REPORT_TTL,
                  lambda: service.rejected_sample(table(), aws("s3"), job_id))


def r_datasets(_q, _m) -> Any:
    return {"datasets": service.datasets(jobs())}


def r_analytics(_q, match) -> Any:
    dataset = match["dataset"]
    return cached(f"analytics:{dataset}", JOBS_TTL, lambda: service.analytics(
        jobs(), aws("s3"), os.environ["BUCKET_NAME"], dataset))


def r_quality(query, _m) -> Any:
    return service.quality(jobs(), param(query, "dataset", DATASET))


def r_sources(_q, _m) -> Any:
    return service.sources(jobs(), env("INGEST_SCHEDULE_TEXT") or "daily")


def r_operations(_q, _m) -> Any:
    return cached("operations", 10, lambda: service.operations(
        jobs(), aws("sqs"), env("QUEUE_URL"), env("DLQ_URL")))


def r_athena_list(_q, _m) -> Any:
    enabled = bool(env("ATHENA_WORKGROUP") and env("GLUE_DATABASE"))
    return {"enabled": enabled,
            "queries": [{"name": k, "title": v["title"], "sql": v["sql"]}
                        for k, v in service.ATHENA_QUERIES.items()]}


def r_athena_run(_q, match) -> Any:
    name = match["name"]
    if name not in service.ATHENA_QUERIES:
        raise service.NotFound(f"unknown query {name!r}")
    if not (env("ATHENA_WORKGROUP") and env("GLUE_DATABASE")):
        raise service.NotFound("analytics (V2) is not enabled on this stack")

    def run() -> dict:
        result = service.run_athena(aws("athena"), os.environ["ATHENA_WORKGROUP"],
                                    os.environ["GLUE_DATABASE"],
                                    service.ATHENA_QUERIES[name]["sql"])
        return {**result, "name": name, "title": service.ATHENA_QUERIES[name]["title"],
                "sql": service.ATHENA_QUERIES[name]["sql"],
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    return cached(f"athena:{name}", ATHENA_TTL, run)


ROUTES: list[tuple[re.Pattern, Callable, int]] = [
    (re.compile(r"^/api/health$"), r_health, 10),
    (re.compile(r"^/api/overview$"), r_overview, CACHE_SECONDS),
    (re.compile(r"^/api/jobs$"), r_jobs, CACHE_SECONDS),
    (re.compile(r"^/api/jobs/(?P<job_id>[0-9a-f]{16})$"), r_job, CACHE_SECONDS),
    (re.compile(r"^/api/jobs/(?P<job_id>[0-9a-f]{16})/rejected$"), r_rejected, 300),
    (re.compile(r"^/api/datasets$"), r_datasets, CACHE_SECONDS),
    (re.compile(r"^/api/analytics/(?P<dataset>[a-z][a-z0-9_]{0,62})$"), r_analytics,
     CACHE_SECONDS),
    (re.compile(r"^/api/quality$"), r_quality, CACHE_SECONDS),
    (re.compile(r"^/api/sources$"), r_sources, CACHE_SECONDS),
    (re.compile(r"^/api/operations$"), r_operations, 10),
    (re.compile(r"^/api/athena$"), r_athena_list, 300),
    (re.compile(r"^/api/athena/(?P<name>[a-z0-9-]{1,40})$"), r_athena_run, ATHENA_TTL),
]


def route(method: str, path: str, query: dict | None) -> dict:
    if method not in ("GET", "HEAD"):
        return response(405, {"error": "read-only API: only GET is allowed"})
    path = path.rstrip("/") or "/"
    for pattern, func, max_age in ROUTES:
        match = pattern.match(path)
        if match:
            try:
                return response(200, func(query or {}, match.groupdict()), max_age)
            except BadRequest as exc:
                return response(400, {"error": str(exc)})
            except service.NotFound as exc:
                return response(404, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the internet
                print(json.dumps({"event": "api_error", "path": path,
                                  "error_type": type(exc).__name__, "error": str(exc),
                                  "trace": traceback.format_exc()[-2000:]}), flush=True)
                return response(500, {"error": "internal error"})
    return response(404, {"error": f"no route for {path}"})


def handler(event: dict, context: Any) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method") or event.get("httpMethod", "GET")
    path = event.get("rawPath") or http.get("path") or event.get("path", "/")
    return route(method, path, event.get("queryStringParameters"))
