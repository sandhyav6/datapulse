"""Render every dashboard page with Streamlit's AppTest against moto-backed jobs."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from etl_processor.app import handle_event
from ingester import app as ingester
from tests.conftest import ROOT, SAMPLE_DIR
from tests.integration.conftest import BUCKET, TABLE, s3_event, sqs_event, upload

APP = str(ROOT / "dashboard" / "app.py")
PAGES = ["views/overview.py", "views/jobs.py", "views/data_quality.py", "views/analytics.py",
         "views/sources.py", "views/operations.py", "views/athena.py"]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture
def dashboard_env(aws, job, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("QUEUE_URL", aws["queue_url"])
    monkeypatch.setenv("DLQ_URL", aws["dlq_url"])
    monkeypatch.setenv("LOG_GROUP", "/aws/lambda/etl-processor-test")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    for name in ("AWS_PROFILE", "ATHENA_WORKGROUP", "GLUE_DATABASE", "INGESTER_FUNCTION"):
        monkeypatch.setenv(name, "")
    st.cache_data.clear()
    st.cache_resource.clear()
    yield aws
    st.cache_data.clear()
    st.cache_resource.clear()


def populate(aws, job):
    for name in ("dirty_small.csv", "clean_small.csv", "poison.csv"):
        key = f"raw/sales/{name}"
        head = upload(aws["s3"], SAMPLE_DIR / name, key)
        for receive in ((1, 2, 3) if name == "poison.csv" else (1,)):
            handle_event(sqs_event(s3_event(key, head), receive), job, "req")
    aws["sqs"].send_message(QueueUrl=aws["dlq_url"], MessageBody="{}")


def populate_public(aws, job):
    """Ingest the two public-dataset fixtures exactly as the scheduled ingester would."""
    for dataset in ("global_temperature", "world_population"):
        source = ingester.Source(dataset=dataset, url=f"https://example.org/{dataset}.csv")
        body = (FIXTURES / f"{dataset}_sample.csv").read_bytes()
        ingester.ingest(source, aws["s3"], BUCKET, lambda url, max_bytes, b=body: b)
        head = aws["s3"].head_object(Bucket=BUCKET, Key=source.key)
        handle_event(sqs_event(s3_event(source.key, head)), job, "req")


def render(page: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if page != PAGES[0]:
        at.switch_page(page)
        at.run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_with_jobs(dashboard_env, job, page):
    populate(dashboard_env, job)
    populate_public(dashboard_env, job)
    at = render(page)
    assert not at.exception, [e.value for e in at.exception]
    errors = [e.value for e in at.error]
    assert not errors or page == "views/operations.py", errors  # operations shows the DLQ error


def test_overview_numbers(dashboard_env, job):
    populate(dashboard_env, job)
    at = render(PAGES[0])
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Files processed"] == "2"
    assert metrics["Failed jobs"] == "1"
    assert metrics["Rows processed"] == "1,100"
    assert metrics["Acceptance rate"] == "95.45%"


def test_operations_shows_dlq_and_failed_job(dashboard_env, job):
    populate(dashboard_env, job)
    at = render("views/operations.py")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Dead-letter queue"] == "1"
    assert any("DLQ" in e.value for e in at.error)
    assert any(df.value.shape[0] == 1 for df in at.dataframe)  # the failed poison job


@pytest.mark.parametrize("page", PAGES)
def test_every_page_handles_no_jobs(dashboard_env, page):
    at = render(page)
    assert not at.exception, [e.value for e in at.exception]


def test_missing_config_shows_setup(dashboard_env, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", "")
    at = render(PAGES[0])
    assert not at.exception
    assert any("not configured" in w.value for w in at.warning)


def test_analytics_shows_latest_snapshot_for_public_data(dashboard_env, job):
    populate_public(dashboard_env, job)
    at = render("views/analytics.py")
    assert not at.exception, [e.value for e in at.exception]
    assert any("Snapshot dataset" in i.value for i in at.info)
    labels = {m.label for m in at.metric}
    assert labels & {"Largest population", "Mean anomaly °C"}


def test_sources_page_lists_public_sources_with_latest_job(dashboard_env, job):
    populate_public(dashboard_env, job)
    at = render("views/sources.py")
    assert not at.exception, [e.value for e in at.exception]
    subheaders = [h.value for h in at.subheader]
    assert "Global temperature" in subheaders and "World population" in subheaders
    latest = [m.value for m in at.metric if m.label == "Latest job"]
    assert latest == ["🟢 SUCCEEDED", "🟢 SUCCEEDED"]
    assert any("INGESTER_FUNCTION" in i.value for i in at.info)  # button needs config
