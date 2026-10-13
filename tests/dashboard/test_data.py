"""Dashboard data layer against moto, using jobs produced by the real pipeline."""

import sys

import pandas as pd
import pytest

from tests.conftest import ROOT, SAMPLE_DIR
from tests.integration.conftest import BUCKET, s3_event, sqs_event, upload

sys.path.insert(0, str(ROOT / "dashboard"))
import data  # noqa: E402
from etl_processor.app import handle_event, make_job_id  # noqa: E402


@pytest.fixture
def populated(aws, job):
    for name in ("dirty_small.csv", "clean_small.csv", "poison.csv"):
        key = f"raw/sales/{name}"
        head = upload(aws["s3"], SAMPLE_DIR / name, key)
        receives = (1, 2, 3) if name == "poison.csv" else (1,)
        for receive in receives:
            handle_event(sqs_event(s3_event(key, head), receive), job, "req")
    return aws


def test_query_jobs_uses_status_index_and_converts_decimals(populated):
    items = data.query_jobs(populated["table"])
    assert sorted(i["status"] for i in items) == ["FAILED", "SUCCEEDED", "SUCCEEDED"]
    succeeded = [i for i in items if i["status"] == "SUCCEEDED"]
    assert all(isinstance(i["rows_total"], int) for i in succeeded)
    assert all(isinstance(i["metrics"]["total_sales"], float) for i in succeeded)


def test_query_jobs_paginates(populated):
    assert len(data.query_jobs(populated["table"], limit_per_status=1)) == 2


def test_jobs_frame(populated):
    df = data.jobs_frame(data.query_jobs(populated["table"]), "Asia/Kolkata")
    assert set(data.JOB_COLUMNS) <= set(df.columns)
    assert str(df.started_at.dt.tz) == "Asia/Kolkata"
    assert df.started_at.is_monotonic_decreasing
    assert set(df.file) == {"dirty_small.csv", "clean_small.csv", "poison.csv"}
    failed = df[df.status == "FAILED"].iloc[0]
    assert failed.error_type == "HeaderValidationError" and failed.rejection_reasons == {}


def test_jobs_frame_empty():
    df = data.jobs_frame([])
    assert df.empty and "file" in df


def test_reason_counts(populated):
    df = data.jobs_frame(data.query_jobs(populated["table"]))
    reasons = data.reason_counts(df)
    assert reasons.groupby("reason")["count"].sum().to_dict() == {
        "duplicate_id": 10, "invalid_transaction_date": 10, "invalid_unit_price": 10,
        "missing_transaction_id": 10, "negative_quantity": 10}


def test_reports_and_aggregates(populated):
    df = data.jobs_frame(data.query_jobs(populated["table"]))
    keys = df[df.status == "SUCCEEDED"].report_key
    reports = [data.get_report(populated["s3"], BUCKET, k) for k in keys]
    combined = data.combine_aggregates(reports)
    assert set(combined) == {"daily_sales", "sales_by_category", "sales_by_region"}
    daily = combined["daily_sales"]
    assert daily["count"].sum() == 950 + 100
    expected = sum(r["metrics"]["total_sales"] for r in reports)
    assert daily["total"].sum() == pytest.approx(expected, abs=0.05)
    assert combined["sales_by_region"]["total"].sum() == pytest.approx(expected, abs=0.05)
    assert combined["sales_by_category"]["total"].is_monotonic_decreasing


def test_csv_sample_reads_a_bounded_range(populated):
    df = data.jobs_frame(data.query_jobs(populated["table"]))
    dirty = df[df.file == "dirty_small.csv"].iloc[0]
    rejected = data.get_csv_sample(populated["s3"], *data.split_s3_uri(dirty.outputs["rejected"]))
    assert len(rejected) == 50 and rejected.columns[-1] == "reasons"
    processed = data.get_csv_sample(populated["s3"],
                                    *data.split_s3_uri(dirty.outputs["processed"]),
                                    rows=10, max_bytes=500)
    assert 0 < len(processed) <= 10 and list(processed.columns)[-1] == "transaction_amount"


def test_queue_depths(aws):
    aws["sqs"].send_message(QueueUrl=aws["dlq_url"], MessageBody="{}")
    assert data.queue_depths(aws["sqs"], aws["queue_url"], aws["dlq_url"]) == {
        "waiting": 0, "in_flight": 0, "dlq": 1}
    assert data.queue_depths(aws["sqs"], None, None) == {
        "waiting": None, "in_flight": None, "dlq": None}


def test_upload_csv(aws):
    result = data.upload_csv(aws["s3"], BUCKET, "sales", "../My Sales (Oct).CSV", b"a,b\n1,2\n")
    assert result["key"] == "raw/sales/My_Sales_Oct_.CSV"
    head = aws["s3"].head_object(Bucket=BUCKET, Key=result["key"])
    assert result["job_id"] == make_job_id(BUCKET, result["key"], head["ETag"].strip('"'))


@pytest.mark.parametrize("dataset, name, body", [
    ("Sales", "x.csv", b"a"), ("../x", "x.csv", b"a"), ("sales", "x.csv", b""),
    ("sales", "x.csv", b"a" * (data.MAX_UPLOAD_BYTES + 1))],
    ids=["uppercase", "traversal", "empty", "too-big"])
def test_upload_csv_rejects_bad_input(aws, dataset, name, body):
    with pytest.raises(ValueError):
        data.upload_csv(aws["s3"], BUCKET, dataset, name, body)


@pytest.mark.parametrize("name, expected", [
    ("data.csv", "data.csv"), ("report", "report.csv"), ("a b/c d.csv", "c_d.csv"),
    ("...", "upload.csv")])
def test_safe_filename(name, expected):
    assert data.safe_filename(name) == expected


@pytest.mark.parametrize("sql", [
    "SELECT 1", "  select * from sales;  ", "WITH t AS (SELECT 1) SELECT * FROM t",
    "-- comment\nSELECT region FROM sales", "SELECT 'drop' AS word"])
def test_validate_select_accepts(sql):
    assert data.validate_select(sql)


@pytest.mark.parametrize("sql", [
    "", "DROP TABLE sales", "SELECT 1; DROP TABLE sales", "INSERT INTO sales VALUES (1)",
    "CREATE TABLE x AS SELECT 1", "/* hi */ DELETE FROM sales", "MSCK REPAIR TABLE sales",
    "SHOW TABLES"])
def test_validate_select_rejects(sql):
    with pytest.raises(ValueError):
        data.validate_select(sql)


class FakeAthena:
    def __init__(self, states):
        self.states = list(states)
        self.stopped = False

    def start_query_execution(self, **kwargs):
        assert kwargs["WorkGroup"] == "wg"
        return {"QueryExecutionId": "q1"}

    def get_query_execution(self, QueryExecutionId):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"QueryExecution": {"Status": {"State": state, "StateChangeReason": "boom"},
                                   "Statistics": {"DataScannedInBytes": 2048,
                                                  "TotalExecutionTimeInMillis": 900}}}

    def get_query_results(self, **kwargs):
        meta = {"ColumnInfo": [{"Name": "region"}, {"Name": "revenue"}]}
        if "NextToken" not in kwargs:
            rows = [["region", "revenue"], ["North", "10.5"]]
            extra = {"NextToken": "t"}
        else:
            rows, extra = [["South", "4"]], {}
        return {"ResultSet": {"ResultSetMetadata": meta,
                              "Rows": [{"Data": [{"VarCharValue": v} for v in r]}
                                       for r in rows]}, **extra}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped = True


def test_run_athena_paginates_and_types_columns():
    df, stats = data.run_athena(FakeAthena(["RUNNING", "SUCCEEDED"]), "wg", "db",
                                "SELECT region, revenue FROM sales", poll=0)
    assert df.to_dict("records") == [{"region": "North", "revenue": 10.5},
                                     {"region": "South", "revenue": 4.0}]
    assert stats == {"query_id": "q1", "bytes_scanned": 2048, "runtime_ms": 900}


def test_run_athena_failure_and_timeout():
    with pytest.raises(RuntimeError, match="boom"):
        data.run_athena(FakeAthena(["FAILED"]), "wg", "db", "SELECT 1", poll=0)
    fake = FakeAthena(["RUNNING"])
    with pytest.raises(TimeoutError):
        data.run_athena(fake, "wg", "db", "SELECT 1", timeout=0, poll=0)
    assert fake.stopped


def test_console_links():
    url = data.logs_console_url("ap-south-1", "/aws/lambda/etl-processor-dev", "abc123")
    assert "log-group/$252Faws$252Flambda$252Fetl-processor-dev/" in url
    assert url.endswith("$3FfilterPattern$3Dabc123")
    assert data.s3_console_url("ap-south-1", "s3://b/reports/x y.json").endswith(
        "prefix=reports/x%20y.json")


def test_config_from_env(monkeypatch):
    for name in ("BUCKET_NAME", "TABLE_NAME", "ATHENA_WORKGROUP"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(data.ConfigError, match="BUCKET_NAME, TABLE_NAME"):
        data.DashboardConfig.from_env()
    monkeypatch.setenv("BUCKET_NAME", "b")
    monkeypatch.setenv("TABLE_NAME", "t")
    cfg = data.DashboardConfig.from_env()
    assert cfg.bucket == "b" and cfg.athena_workgroup is None


def test_plain():
    from decimal import Decimal
    assert data.plain({"a": [Decimal("1"), Decimal("1.5")]}) == {"a": [1, 1.5]}
    assert isinstance(pd.DataFrame([data.plain({"x": Decimal("2")})]).x[0].item(), int)


def test_aggregate_frames_by_type():
    specs = {"d": {"type": "daily"}, "y": {"type": "yearly"}, "g": {"type": "group"}}
    frames = data.aggregate_frames({
        "d": [{"date": "2026-10-01", "total": 5.0, "count": 2}],
        "y": {"2025": 2.0, "1960": 1.0},
        "g": {"small": 1.0, "big": 9.0}}, specs)
    assert frames["d"][0] == "daily" and str(frames["d"][1].date.dtype).startswith("datetime")
    assert frames["y"][1].to_dict("records") == [{"year": 1960, "value": 1.0},
                                                 {"year": 2025, "value": 2.0}]
    assert list(frames["g"][1].key) == ["big", "small"]


def test_combined_frames_sum_incremental_reports():
    specs = {"by_year": {"type": "yearly"}, "by_cat": {"type": "group"}}
    reports = [{"aggregates": {"by_year": {"2025": 1.0}, "by_cat": {"a": 1.0}}},
               {"aggregates": {"by_year": {"2024": 3.0, "2025": 2.0}, "by_cat": {"a": 2.0}}}]
    frames = data.combined_frames(reports, specs)
    assert frames["by_year"][1].to_dict("records") == [{"year": 2024, "value": 3.0},
                                                       {"year": 2025, "value": 3.0}]
    assert frames["by_cat"][1].to_dict("records") == [{"key": "a", "value": 3.0}]


def test_sources_and_schemas_shipped():
    datasets = [s["dataset"] for s in data.load_sources()]
    assert datasets == ["global_temperature", "world_population"]
    assert set(datasets) <= set(data.known_datasets())
    assert "sales" in data.known_datasets()
    assert "World Bank" in data.schema_description("world_population")
    assert data.schema_description("nope") == ""


def test_latest_jobs(populated):
    df = data.jobs_frame(data.query_jobs(populated["table"]))
    latest = data.latest_jobs(df)
    assert list(latest.dataset) == ["sales"] and latest.iloc[0].job_id == df.iloc[0].job_id
    assert data.latest_jobs(df.iloc[0:0]).empty


def test_trigger_ingest_is_async():
    class FakeLambda:
        def invoke(self, **kwargs):
            self.kwargs = kwargs
            return {"StatusCode": 202}

    fake = FakeLambda()
    assert data.trigger_ingest(fake, "dataset-ingester-dev", ["world_population"]) == 202
    assert fake.kwargs["InvocationType"] == "Event"
    assert fake.kwargs["Payload"] == b'{"datasets": ["world_population"]}'
