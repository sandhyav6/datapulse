"""The public read-only API, end to end against moto with jobs produced by the real pipeline."""

import json

import pytest

from api import app as api
from api import service
from etl_processor.app import handle_event
from ingester import app as ingester
from tests.conftest import ROOT, SAMPLE_DIR
from tests.integration.conftest import BUCKET, TABLE, s3_event, sqs_event, upload

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def api_env(aws, job, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("QUEUE_URL", aws["queue_url"])
    monkeypatch.setenv("DLQ_URL", aws["dlq_url"])
    monkeypatch.setenv("STAGE", "test")
    monkeypatch.setenv("INGEST_SCHEDULE_TEXT", "daily at 06:30 IST")
    for name in ("ATHENA_WORKGROUP", "GLUE_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "_clients", {})
    monkeypatch.setattr(api, "_cache", {})
    return aws


def populate(aws, job):
    for name in ("dirty_small.csv", "clean_small.csv", "poison.csv"):
        key = f"raw/sales/{name}"
        head = upload(aws["s3"], SAMPLE_DIR / name, key)
        for receive in ((1, 2, 3) if name == "poison.csv" else (1,)):
            handle_event(sqs_event(s3_event(key, head), receive), job, "req")
    for dataset in ("global_temperature", "world_population"):
        source = ingester.Source(dataset=dataset, url=f"https://example.org/{dataset}.csv")
        body = (FIXTURES / f"{dataset}_sample.csv").read_bytes()
        ingester.ingest(source, aws["s3"], BUCKET, lambda url, max_bytes, b=body: b)
        head = aws["s3"].head_object(Bucket=BUCKET, Key=source.key)
        handle_event(sqs_event(s3_event(source.key, head)), job, "req")
    aws["sqs"].send_message(QueueUrl=aws["dlq_url"], MessageBody="{}")


def get(path: str, query: dict | None = None, method: str = "GET") -> tuple[int, dict, dict]:
    event = {"rawPath": path, "queryStringParameters": query,
             "requestContext": {"http": {"method": method, "path": path}}}
    resp = api.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]), resp["headers"]


def job_id_for(body: dict, file: str) -> str:
    return next(j["job_id"] for j in body["jobs"] if j["file"] == file)


def test_health(api_env):
    status, body, headers = get("/api/health")
    assert status == 200 and body == {"ok": True, "stage": "test", "analytics": False}
    assert headers["cache-control"] == "public, max-age=10"


def test_overview(api_env, job):
    populate(api_env, job)
    status, body, headers = get("/api/overview")
    assert status == 200
    kpis = body["kpis"]
    assert kpis["files_processed"] == 4 and kpis["failed_jobs"] == 1
    assert kpis["rows_processed"] == 1000 + 100 + 11 + 12
    assert kpis["datasets"] == 3
    assert kpis["p95_duration_ms"] is not None
    assert sum(d["SUCCEEDED"] for d in body["jobs_per_day"]) == 4
    assert len(body["latest"]) == 5
    assert headers["cache-control"] == "public, max-age=30"


def test_jobs_filters(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/jobs")
    assert len(body["jobs"]) == 5
    assert all("error_message" not in j or j["status"] == "FAILED" for j in body["jobs"])
    _, failed, _ = get("/api/jobs", {"status": "FAILED"})
    assert [j["file"] for j in failed["jobs"]] == ["poison.csv"]
    _, sales, _ = get("/api/jobs", {"dataset": "sales", "limit": "2"})
    assert len(sales["jobs"]) == 2 and {j["dataset"] for j in sales["jobs"]} == {"sales"}


@pytest.mark.parametrize("query", [{"status": "DONE"}, {"dataset": "../x"}, {"limit": "abc"}])
def test_jobs_rejects_bad_input(api_env, query):
    assert get("/api/jobs", query)[0] == 400


def test_job_detail_and_rejected_sample(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/jobs")
    dirty = job_id_for(body, "dirty_small.csv")
    status, detail, _ = get(f"/api/jobs/{dirty}")
    assert status == 200
    assert detail["job"]["rows_rejected"] == 50
    assert detail["report"]["rejection_reasons"]["duplicate_id"] == 10
    assert set(detail["job"]["outputs"]) == {"processed", "rejected"}
    status, sample, headers = get(f"/api/jobs/{dirty}/rejected")
    assert status == 200 and sample["columns"][-1] == "reasons" and len(sample["rows"]) == 50
    assert headers["cache-control"] == "public, max-age=300"
    clean = job_id_for(body, "clean_small.csv")
    assert get(f"/api/jobs/{clean}/rejected")[1] == {"columns": [], "rows": []}


def test_unknown_job_and_route(api_env):
    assert get("/api/jobs/0123456789abcdef")[0] == 404
    assert get("/api/jobs/not-a-job")[0] == 404  # does not even match a route
    assert get("/api/nope")[0] == 404


def test_read_only(api_env):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, body, headers = get("/api/jobs", method=method)
        assert status == 405 and "read-only" in body["error"]
        assert headers["cache-control"] == "no-store"


def test_datasets(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/datasets")
    by_name = {d["name"]: d for d in body["datasets"]}
    assert {"sales", "world_population", "global_temperature", "returns"} <= set(by_name)
    assert by_name["world_population"]["load_type"] == "snapshot"
    assert by_name["sales"]["jobs"] == 3 and by_name["sales"]["succeeded"] == 2
    assert by_name["returns"]["latest"] is None  # schema shipped, no jobs yet


def test_analytics_snapshot(api_env, job):
    populate(api_env, job)
    status, body, _ = get("/api/analytics/world_population")
    assert status == 200 and body["mode"] == "snapshot" and body["source"]["rows"] == 9
    charts = {c["name"]: c for c in body["charts"]}
    world = charts["world_population_by_year"]
    assert world["type"] == "yearly" and world["where"] == {"country_code": "WLD"}
    assert world["points"] == [{"year": 2024, "value": 8140897523},
                               {"year": 2025, "value": 8215424893}]
    ranking = charts["peak_population_by_country"]
    assert [p["key"] for p in ranking["points"]] == ["India", "China", "Aruba"]
    assert ranking["exclude"] == {"country_code": 49}
    assert {m["name"]: m["value"] for m in body["metrics"]}["largest_population"] == 8215424893


def test_analytics_incremental_combines_files(api_env, job):
    populate(api_env, job)
    status, body, _ = get("/api/analytics/sales")
    assert status == 200 and body["mode"] == "incremental" and body["source"]["files"] == 2
    metrics = {m["name"]: m["value"] for m in body["metrics"]}
    assert metrics["clean_rows"] == 1050
    daily = next(c for c in body["charts"] if c["type"] == "daily")
    assert sum(p["count"] for p in daily["points"]) == 1050
    assert metrics["total_value"] == pytest.approx(sum(p["total"] for p in daily["points"]),
                                                   abs=0.05)


def test_analytics_unknown_dataset(api_env, job):
    populate(api_env, job)
    assert get("/api/analytics/returns")[0] == 404
    assert get("/api/analytics/Bad-Name")[0] == 404


def test_quality(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/quality", {"dataset": "sales"})
    assert body["rows_checked"] == 1100 and body["rows_rejected"] == 50
    assert {r["reason"]: r["count"] for r in body["reasons"]}["negative_quantity"] == 10
    assert len(body["per_job"]) == 2
    _, everything, _ = get("/api/quality")
    assert everything["rows_checked"] == 1123


def test_sources(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/sources")
    assert body["schedule"] == "daily at 06:30 IST"
    by_name = {s["dataset"]: s for s in body["sources"]}
    assert by_name["world_population"]["latest"]["status"] == "SUCCEEDED"
    assert by_name["global_temperature"]["license"] == "CC-BY-4.0"
    assert by_name["world_population"]["raw_key"] == "raw/world_population/world_population.csv"


def test_operations(api_env, job):
    populate(api_env, job)
    _, body, _ = get("/api/operations")
    assert body["queues"] == {"waiting": 0, "in_flight": 0, "dlq": 1}
    assert [j["file"] for j in body["failed"]] == ["poison.csv"]
    assert body["failed"][0]["error_type"] == "HeaderValidationError"
    assert body["retrying"] == []


def test_athena_disabled(api_env):
    _, body, _ = get("/api/athena")
    assert body["enabled"] is False and len(body["queries"]) == 3
    assert get("/api/athena/monthly-revenue")[0] == 404
    assert get("/api/athena/drop-everything")[0] == 404


class FakeAthena:
    def __init__(self):
        self.calls = 0

    def start_query_execution(self, **kwargs):
        self.calls += 1
        assert kwargs["WorkGroup"] == "wg" and kwargs["QueryString"].startswith("SELECT")
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"},
                                   "Statistics": {"DataScannedInBytes": 6839,
                                                  "TotalExecutionTimeInMillis": 700}}}

    def get_query_results(self, **kwargs):
        rows = [["month", "revenue"], ["2026-09", "543194.63"]]
        return {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [{"Name": "month"},
                                                                  {"Name": "revenue"}]},
                              "Rows": [{"Data": [{"VarCharValue": v} for v in r]}
                                       for r in rows]}}


def test_athena_runs_only_saved_queries_and_caches(api_env, monkeypatch):
    monkeypatch.setenv("ATHENA_WORKGROUP", "wg")
    monkeypatch.setenv("GLUE_DATABASE", "db")
    fake = FakeAthena()
    api._clients["athena"] = fake
    status, body, headers = get("/api/athena/monthly-revenue")
    assert status == 200 and body["rows"] == [["2026-09", "543194.63"]]
    assert body["bytes_scanned"] == 6839 and body["columns"] == ["month", "revenue"]
    assert headers["cache-control"] == "public, max-age=900"
    get("/api/athena/monthly-revenue")
    assert fake.calls == 1  # second call served from the Lambda's cache


def test_internal_errors_do_not_leak(api_env, monkeypatch, capsys):
    def boom():
        raise RuntimeError("secret detail")
    monkeypatch.setattr(api, "jobs", boom)
    status, body, _ = get("/api/overview")
    assert status == 500 and body == {"error": "internal error"}
    assert "secret detail" in capsys.readouterr().out  # logged for the operator only


def test_service_helpers():
    assert service.plain({"a": [service.Decimal("1"), service.Decimal("2.5")]}) == {
        "a": [1, 2.5]}
    assert service.split_s3_uri("s3://b/k/x.csv") == ("b", "k/x.csv")
    assert service.overview([])["kpis"]["acceptance_rate"] is None
