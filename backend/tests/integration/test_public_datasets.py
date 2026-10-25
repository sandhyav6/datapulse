"""Public datasets end to end: ingester -> raw/ -> S3 event -> pipeline -> report.

Fixtures are small files in the exact format of the real sources (same headers, value styles
and quirks: month-only dates, 4-dp decimals, World Bank aggregate rows), plus injected bad rows.
"""

import json

from etl_processor.app import handle_event
from ingester import app as ingester
from tests.conftest import ROOT, SCHEMA_DIR
from tests.integration.conftest import BUCKET, read_text, s3_event, sqs_event

FIXTURES = ROOT / "tests" / "fixtures"


def ingest_and_process(aws, job, dataset: str, fixture: str) -> dict:
    source = ingester.Source(dataset=dataset, url=f"https://example.org/{dataset}.csv")
    body = (FIXTURES / fixture).read_bytes()
    result = ingester.ingest(source, aws["s3"], BUCKET, lambda url, max_bytes: body)
    assert result["status"] == "uploaded"
    head = aws["s3"].head_object(Bucket=BUCKET, Key=source.key)
    assert handle_event(sqs_event(s3_event(source.key, head)), job, "req") == {
        "batchItemFailures": []}
    [item] = aws["table"].scan()["Items"]
    return json.loads(read_text(aws["s3"], item["report_key"]))


def test_global_temperature(aws, job):
    report = ingest_and_process(aws, job, "global_temperature", "global_temperature_sample.csv")
    assert report["load_type"] == "snapshot"
    assert report["counts"] == {"total": 11, "accepted": 8, "rejected": 3}
    assert report["rejection_reasons"] == {"duplicate_id": 1, "invalid_year": 1,
                                           "invalid_mean": 1}
    aggs = report["aggregates"]
    assert aggs["anomaly_by_year_gistemp"] == {"2024": 1.36}  # mean of 1.24, 1.48, 1.36
    assert aggs["anomaly_by_year_gcag"] == {"1850": -0.5737, "2024": 1.3068}  # 4 dp, half-up
    assert aggs["warmest_month_by_source"] == {"GISTEMP": 1.48, "GCAG": 1.3481}
    assert report["metrics"]["warmest_month_°C"] == 1.48
    assert report["aggregate_specs"]["anomaly_by_year_gcag"] == {
        "type": "yearly", "column": "year", "func": "mean", "where": {"source": "GCAG"},
        "exclude": {}}


def test_world_population(aws, job):
    report = ingest_and_process(aws, job, "world_population", "world_population_sample.csv")
    assert report["counts"] == {"total": 12, "accepted": 9, "rejected": 3}
    assert report["rejection_reasons"] == {"below_min_year": 1, "negative_value": 1,
                                           "duplicate_id": 1}
    aggs = report["aggregates"]
    assert aggs["world_population_by_year"] == {"2024": 8140897523, "2025": 8215424893}
    assert aggs["india_population_by_year"] == {"2024": 1450935791, "2025": 1463865525}
    # World Bank aggregates (World, Arab World) are excluded from the country ranking
    assert aggs["peak_population_by_country"] == {"India": 1463865525, "China": 1408975000,
                                                  "Aruba": 55578}
    assert report["metrics"] == {"largest_population": 8215424893, "rows": 9}


def test_processed_output_keeps_full_precision(aws, job):
    ingest_and_process(aws, job, "global_temperature", "global_temperature_sample.csv")
    [item] = aws["table"].scan()["Items"]
    key = item["outputs"]["processed"].split(BUCKET + "/")[1]
    lines = read_text(aws["s3"], key).splitlines()
    assert lines[0] == "source,year,mean"
    assert lines[1] == "GCAG,1850-01-01,-0.7337"  # month -> first day; 4 dp kept


def test_schemas_ship_with_the_lambda():
    for dataset in ("global_temperature", "world_population"):
        assert (SCHEMA_DIR / f"{dataset}.json").is_file()
