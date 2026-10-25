"""Benchmark rendering and teardown, without touching AWS (moto for S3)."""

import sys

from tests.conftest import ROOT
from tests.integration.conftest import BUCKET

sys.path.insert(0, str(ROOT / "scripts"))
import run_benchmark  # noqa: E402
import teardown  # noqa: E402


def job(label, rows, ms, e2e=3.2):
    return run_benchmark.JobResult(label=label, rows=rows, size_bytes=rows * 50,
                                   status="SUCCEEDED", duration_ms=ms, accepted=rows - 5,
                                   rejected=5, acceptance_rate=99.5, end_to_end_s=e2e,
                                   job_id="abc")


def test_rows_per_second():
    assert job("x", 10_000, 2000).rows_per_second == 5000
    missing = run_benchmark.to_result("x", 100, 1, "j", None, None)
    assert missing.status == "TIMEOUT" and missing.rows_per_second is None


def test_to_result_reads_dynamodb_item():
    from decimal import Decimal
    item = {"status": "SUCCEEDED", "duration_ms": Decimal(1234), "rows_total": Decimal(1000),
            "rows_accepted": Decimal(950), "rows_rejected": Decimal(50),
            "acceptance_rate": Decimal("95.0")}
    result = run_benchmark.to_result("1k", 1000, 50_000, "j1", item, 4.0)
    assert (result.duration_ms, result.accepted, result.acceptance_rate) == (1234, 950, 95.0)


def test_p95():
    assert run_benchmark.p95([]) is None
    assert run_benchmark.p95([7]) == 7
    assert run_benchmark.p95(list(range(1, 101))) == 95


def test_render_contains_measured_numbers_only():
    burst = run_benchmark.BurstResult(files=20, succeeded=20, failed=0, drain_s=41.3,
                                      peak_queue_depth=18, peak_concurrency=2, throttles=0,
                                      durations_ms=[400, 420, 900])
    md = run_benchmark.render([job("1,000 rows", 1000, 400), job("50,000 rows", 50000, 9000)],
                              burst, {"when": "2026-10-20 10:00 UTC", "stack": "s",
                                      "region": "ap-south-1"})
    assert "| 50,000 rows | 2,441 KB | SUCCEEDED | 9,000 ms | 5,556 |" in md
    assert "| Peak Lambda concurrency | 2 |" in md
    assert "| Jobs succeeded / failed | 20 / 0 |" in md
    assert "p95 job duration across all benchmark jobs: **" in md


def test_render_without_burst_or_metrics():
    md = run_benchmark.render([run_benchmark.to_result("x", 1, 1, "j", None, None)], None,
                              {"when": "w", "stack": "s", "region": "r"})
    assert "Burst" not in md and "| TIMEOUT | — |" in md


def test_empty_bucket_removes_every_version(aws):
    s3 = aws["s3"]
    for body in (b"1", b"2", b"3"):
        s3.put_object(Bucket=BUCKET, Key="raw/sales/a.csv", Body=body)
    s3.put_object(Bucket=BUCKET, Key="reports/x.json", Body=b"{}")
    s3.delete_object(Bucket=BUCKET, Key="reports/x.json")  # leaves a delete marker
    assert teardown.empty_bucket(s3, BUCKET) == 5  # 3 + 1 versions + 1 delete marker
    listing = s3.list_object_versions(Bucket=BUCKET)
    assert not listing.get("Versions") and not listing.get("DeleteMarkers")
    s3.delete_bucket(Bucket=BUCKET)  # now possible, which is what sam delete needs


import compare_formats  # noqa: E402


def test_compare_formats_render():
    rows = [{"name": "Top 10 categories", "csv": {"bytes": 2_000_000, "ms": 900},
             "parquet": {"bytes": 200_000, "ms": 700}},
            {"name": "Row count", "csv": {"bytes": 0, "ms": 10},
             "parquet": {"bytes": 0, "ms": 10}}]
    md = compare_formats.render(rows, {"when": "w", "workgroup": "wg", "database": "db",
                                       "job_id": "abc", "rows": "950"})
    assert "| Top 10 categories | 2,000,000 B | 200,000 B | 90.0% | 900 ms | 700 ms |" in md
    assert "| Row count | 0 B | 0 B | — |" in md
    assert "**90.0% less data scanned**" in md
    assert "same 950 rows" in md and "job `abc`" in md


def test_compare_formats_queries_target_both_tables():
    for template in compare_formats.QUERIES.values():
        assert "{table}" in template
        assert template.format(table="sales").lstrip().upper().startswith("SELECT")


def test_compare_formats_temp_tables_point_at_one_job():
    uri = "s3://b/processed/dataset=sales/ingest_date=2026-10-25/job_id=abc/part-0000.csv"
    assert compare_formats.folder(uri) == (
        "s3://b/processed/dataset=sales/ingest_date=2026-10-25/job_id=abc/")
    csv_ddl = compare_formats.ddl("cmp_csv_abc", "s3://b/p/", "csv")
    assert "STORED AS TEXTFILE LOCATION 's3://b/p/'" in csv_ddl
    assert "'skip.header.line.count'='1'" in csv_ddl
    assert "STORED AS PARQUET LOCATION 's3://b/c/'" in compare_formats.ddl(
        "cmp_parquet_abc", "s3://b/c/", "parquet")


import deploy_frontend  # noqa: E402


def test_frontend_zip_has_index_at_root(tmp_path):
    import io
    import zipfile
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("x")
    (tmp_path / "index.html").write_text("<html>")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    names = zipfile.ZipFile(io.BytesIO(deploy_frontend.zip_dist(tmp_path))).namelist()
    assert sorted(names) == ["assets/index-abc123.js", "favicon.svg", "index.html"]


def test_frontend_deploy_waits_for_amplify_job(monkeypatch):
    class FakeAmplify:
        def __init__(self):
            self.states = ["PENDING", "RUNNING", "SUCCEED"]

        def get_job(self, **kwargs):
            assert kwargs["branchName"] == "main"
            return {"job": {"summary": {"status": self.states.pop(0)}}}

    monkeypatch.setattr(deploy_frontend.time, "sleep", lambda seconds: None)
    assert deploy_frontend.wait_for_job(FakeAmplify(), "app", "1") == "SUCCEED"
