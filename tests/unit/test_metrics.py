import json

from etl_processor import metrics


def test_emf_shape():
    payload = metrics.emf_payload("sales", {"RowsAccepted": (5, "Count")}, {"job_id": "j"},
                                  timestamp_ms=123)
    assert payload == {
        "_aws": {"Timestamp": 123, "CloudWatchMetrics": [{
            "Namespace": "EtlPipeline", "Dimensions": [[]],
            "Metrics": [{"Name": "RowsAccepted", "Unit": "Count"}]}]},
        "Dataset": "sales", "RowsAccepted": 5, "job_id": "j"}


def test_job_succeeded_emits_four_metrics_on_one_line(capsys):
    metrics.job_succeeded("sales", job_id="j1", accepted=950, rejected=50, duration_ms=1234)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    declared = payload["_aws"]["CloudWatchMetrics"][0]["Metrics"]
    assert {m["Name"]: m["Unit"] for m in declared} == {
        "RowsAccepted": "Count", "RowsRejected": "Count",
        "JobDurationMs": "Milliseconds", "JobsFailed": "Count"}
    assert (payload["RowsAccepted"], payload["RowsRejected"], payload["JobsFailed"]) == (
        950, 50, 0)
    assert payload["Dataset"] == "sales" and payload["job_id"] == "j1"


def test_job_failed():
    payload = metrics.job_failed("unknown", job_id="j2", duration_ms=5)
    assert payload["JobsFailed"] == 1
    assert payload["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]  # 4 metrics total
    assert payload["Dataset"] == "unknown"  # still searchable as a property
