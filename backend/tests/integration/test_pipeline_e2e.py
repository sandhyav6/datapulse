"""End-to-end handler tests against moto S3, DynamoDB and SQS."""

import csv
import io
import json
import shutil

import pytest
from boto3.dynamodb.conditions import Key

from etl_processor import app
from etl_processor.app import EtlJob, handle_event, make_job_id
from etl_processor.config import Settings
from etl_processor.jobs import JobRepository
from etl_processor.storage import S3Storage
from tests.conftest import ROOT, SAMPLE_DIR, SCHEMA_DIR, expected_counts
from tests.integration.conftest import (
    BUCKET,
    TABLE,
    list_keys,
    read_text,
    s3_event,
    sqs_event,
    upload,
)

DIRTY = SAMPLE_DIR / "dirty_small.csv"
POISON = SAMPLE_DIR / "poison.csv"


def run(job, key, head, receive_count=1, message_id="msg-1"):
    return handle_event(sqs_event(s3_event(key, head), receive_count, message_id), job, "req")


def job_item(aws, key, head):
    job_id = make_job_id(BUCKET, key, head["ETag"].strip('"'))
    return job_id, aws["table"].get_item(Key={"job_id": job_id}).get("Item")


def csv_rows(text):
    return list(csv.reader(io.StringIO(text)))


def all_output_keys(s3):
    return list_keys(s3, "processed/") + list_keys(s3, "rejected/") + list_keys(s3, "reports/")


def test_normal_file_produces_one_set_of_outputs(aws, job):
    key = "raw/sales/dirty_small.csv"
    head = upload(aws["s3"], DIRTY, key)
    assert run(job, key, head) == {"batchItemFailures": []}

    job_id, item = job_item(aws, key, head)
    expected = expected_counts(DIRTY)
    assert item["status"] == "SUCCEEDED"
    assert item["attempts"] == 1
    assert (item["rows_total"], item["rows_accepted"], item["rows_rejected"]) == (
        expected["rows"], expected["accepted"], expected["rejected"])
    assert item["rejection_reasons"] == expected["rejection_reasons"]
    assert float(item["metrics"]["total_sales"]) == expected["total_sales"]
    assert "lease_expires_at" not in item

    day = item["ingest_date"]
    assert all_output_keys(aws["s3"]) == sorted([
        f"processed/dataset=sales/ingest_date={day}/job_id={job_id}/part-0000.csv",
        f"rejected/dataset=sales/job_id={job_id}/rejected.csv",
        f"reports/dataset=sales/{job_id}.json",
    ])
    processed = csv_rows(read_text(aws["s3"], item["outputs"]["processed"].split(BUCKET + "/")[1]))
    assert processed[0][-1] == "transaction_amount"
    assert len(processed) - 1 == expected["accepted"]
    rejected_key = f"rejected/dataset=sales/job_id={job_id}/rejected.csv"
    rejected = csv_rows(read_text(aws["s3"], rejected_key))
    assert rejected[0][-1] == "reasons" and len(rejected) - 1 == expected["rejected"]

    report = json.loads(read_text(aws["s3"], item["report_key"]))
    assert report["job_id"] == job_id and report["status"] == "SUCCEEDED"
    assert report["counts"]["accepted"] == expected["accepted"]
    assert report["source"] == f"s3://{BUCKET}/{key}"

    by_status = aws["table"].query(IndexName="status-index",
                                   KeyConditionExpression=Key("status").eq("SUCCEEDED"))
    assert [i["job_id"] for i in by_status["Items"]] == [job_id]


def test_duplicate_delivery_is_idempotent(aws, job, capsys):
    key = "raw/sales/dirty_small.csv"
    head = upload(aws["s3"], DIRTY, key)
    run(job, key, head)
    _, first = job_item(aws, key, head)
    keys_before = all_output_keys(aws["s3"])
    report_before = read_text(aws["s3"], first["report_key"])
    capsys.readouterr()

    for i in range(3):
        assert run(job, key, head, message_id=f"replay-{i}") == {"batchItemFailures": []}

    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [entry["event"] for entry in logs] == ["duplicate_delivery"] * 3
    _, after = job_item(aws, key, head)
    assert after == first  # same attempts, totals, timestamps
    assert all_output_keys(aws["s3"]) == keys_before
    assert read_text(aws["s3"], first["report_key"]) == report_before


def test_poison_file_is_failed_on_every_receive_and_ends_failed(aws, job):
    key = "raw/sales/poison.csv"
    head = upload(aws["s3"], POISON, key)
    for receive in (1, 2, 3):
        assert run(job, key, head, receive_count=receive) == {
            "batchItemFailures": [{"itemIdentifier": "msg-1"}]}
        _, item = job_item(aws, key, head)
        assert item["status"] == "FAILED"
        assert item["attempts"] == receive
    assert item["error_type"] == "HeaderValidationError"
    assert "unit_price" in item["error_message"]
    assert list_keys(aws["s3"], "processed/") == []


def test_crash_after_processed_upload_recovers_without_duplicates(aws, job, monkeypatch):
    key = "raw/sales/dirty_small.csv"
    head = upload(aws["s3"], DIRTY, key)
    real_upload = S3Storage.upload_file
    calls = []

    def crash_after_processed(self, path, out_key, content_type):
        uri = real_upload(self, path, out_key, content_type)
        calls.append(out_key)
        if out_key.startswith("processed/") and len(calls) == 1:
            raise ConnectionError("simulated crash after processed upload")
        return uri

    monkeypatch.setattr(S3Storage, "upload_file", crash_after_processed)
    assert run(job, key, head, receive_count=1)["batchItemFailures"]
    _, item = job_item(aws, key, head)
    assert item["status"] == "PROCESSING"
    assert item["lease_expires_at"] == 0  # released so the retry can claim immediately
    assert item["error_type"] == "ConnectionError"

    assert run(job, key, head, receive_count=2) == {"batchItemFailures": []}
    job_id, item = job_item(aws, key, head)
    assert item["status"] == "SUCCEEDED" and item["attempts"] == 2
    assert "error_type" not in item
    processed_keys = list_keys(aws["s3"], "processed/")
    assert len(processed_keys) == 1  # same deterministic key overwritten, not a second file
    rows = csv_rows(read_text(aws["s3"], processed_keys[0]))[1:]
    assert len(rows) == expected_counts(DIRTY)["accepted"]
    assert len({r[0] for r in rows}) == len(rows)


def test_hard_crash_lease_blocks_until_expiry(aws, job, clock):
    """A crashed attempt never releases its lease; others must wait until it expires."""
    key = "raw/sales/dirty_small.csv"
    head = upload(aws["s3"], DIRTY, key)
    job_id = make_job_id(BUCKET, key, head["ETag"].strip('"'))
    job.jobs.create_or_claim(job_id, attempt_id="crashed", dataset="sales", source_key=key,
                             source_etag=head["ETag"].strip('"'))

    assert run(job, key, head, receive_count=2) == {
        "batchItemFailures": [{"itemIdentifier": "msg-1"}]}
    clock.now += 301
    assert run(job, key, head, receive_count=3) == {"batchItemFailures": []}
    _, item = job_item(aws, key, head)
    assert item["status"] == "SUCCEEDED" and item["attempts"] == 2
    # The crashed attempt waking up late cannot overwrite the newer result.
    assert job.jobs.mark_failed(job_id, "crashed", error_type="X", error_message="late",
                                duration_ms=1) is False
    assert job_item(aws, key, head)[1]["status"] == "SUCCEEDED"


def test_retryable_error_marked_failed_only_on_last_receive(aws, job, monkeypatch):
    key = "raw/sales/clean_small.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", key)

    def boom(*args, **kwargs):
        raise TimeoutError("S3 timed out")

    monkeypatch.setattr(S3Storage, "upload_file", boom)
    run(job, key, head, receive_count=1)
    assert job_item(aws, key, head)[1]["status"] == "PROCESSING"
    run(job, key, head, receive_count=3)
    _, item = job_item(aws, key, head)
    assert item["status"] == "FAILED" and item["error_type"] == "TimeoutError"
    assert item["attempts"] == 2


def test_failed_job_is_reclaimed_after_fix_and_redrive(aws, tmp_path):
    """Recovery demo: a file for an unknown dataset fails, the schema is deployed, the DLQ
    message is redriven, and the same job is reclaimed (attempts = 4) and succeeds."""
    shutil.copy(SCHEMA_DIR / "sales.json", tmp_path / "sales.json")
    settings = Settings(bucket_name=BUCKET, table_name=TABLE, schema_dir=tmp_path)
    job = EtlJob(settings, S3Storage(aws["s3"], BUCKET), JobRepository(aws["table"]))
    key = "raw/returns/returns_small.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "returns_small.csv", key)

    for receive in (1, 2, 3):
        run(job, key, head, receive_count=receive)
    _, item = job_item(aws, key, head)
    assert item["status"] == "FAILED" and item["error_type"] == "UnknownDatasetError"

    shutil.copy(ROOT / "demo" / "returns.json", tmp_path / "returns.json")
    assert run(job, key, head, receive_count=1) == {"batchItemFailures": []}
    _, item = job_item(aws, key, head)
    assert item["status"] == "SUCCEEDED" and item["attempts"] == 4
    assert item["rows_accepted"] == 50
    assert "refunds_by_reason" in json.loads(read_text(aws["s3"], item["report_key"]))[
        "aggregates"]


def test_job_reads_the_uploaded_version_not_a_later_overwrite(aws, job):
    key = "raw/sales/data.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", key)
    upload(aws["s3"], POISON, key)  # overwritten before the queued job runs
    assert run(job, key, head) == {"batchItemFailures": []}
    assert job_item(aws, key, head)[1]["rows_accepted"] == 100


def test_url_encoded_key(aws, job):
    key = "raw/sales/october sales (final).csv"
    head = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", key)
    event = s3_event(key, head)
    event["Records"][0]["s3"]["object"]["key"] = "raw/sales/october+sales+%28final%29.csv"
    assert handle_event(sqs_event(event), job, "req") == {"batchItemFailures": []}
    assert job_item(aws, key, head)[1]["source_key"] == key


def test_s3_test_event_is_ignored(aws, job):
    body = {"Service": "Amazon S3", "Event": "s3:TestEvent", "Bucket": BUCKET}
    assert handle_event(sqs_event(body), job, "req") == {"batchItemFailures": []}


@pytest.mark.parametrize("body", ["not json", json.dumps({"hello": "world"})])
def test_garbage_message_is_a_batch_failure(aws, job, body):
    event = sqs_event({})
    event["Records"][0]["body"] = body
    assert handle_event(event, job, "req") == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}


def test_key_outside_dataset_folder_is_poison(aws, job):
    key = "raw/loose.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", key)
    assert run(job, key, head)["batchItemFailures"]
    assert job_item(aws, key, head)[1]["error_type"] == "UnknownDatasetError"


def test_invalid_utf8_is_poison(aws, job):
    key = "raw/sales/binary.csv"
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=b"transaction_id\n\xff\xfe\xfa\n")
    head = aws["s3"].head_object(Bucket=BUCKET, Key=key)
    assert run(job, key, head)["batchItemFailures"]
    _, item = job_item(aws, key, head)
    assert item["status"] == "FAILED" and item["error_type"] == "UnreadableFileError"


def test_batch_with_mixed_records(aws, job):
    good_key, bad_key = "raw/sales/good.csv", "raw/sales/bad.csv"
    good = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", good_key)
    bad = upload(aws["s3"], POISON, bad_key)
    event = {"Records": [sqs_event(s3_event(good_key, good), message_id="m-good")["Records"][0],
                         sqs_event(s3_event(bad_key, bad), message_id="m-bad")["Records"][0]]}
    assert handle_event(event, job, "req") == {"batchItemFailures": [{"itemIdentifier": "m-bad"}]}


def test_lambda_entry_point_reads_environment(aws, monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("SCHEMA_DIR", str(SCHEMA_DIR))
    monkeypatch.setattr(app, "_clients", {})
    key = "raw/sales/clean_small.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "clean_small.csv", key)

    class Context:
        aws_request_id = "lambda-req-1"

    assert app.handler(sqs_event(s3_event(key, head)), Context()) == {"batchItemFailures": []}
    assert job_item(aws, key, head)[1]["attempt_id"] == "lambda-req-1:msg-1"
