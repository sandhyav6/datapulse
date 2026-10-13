from datetime import date
from decimal import Decimal

import pytest

from etl_processor.config import load_dataset_schema, parse_schema
from etl_processor.pipeline import process_rows
from etl_processor.reporter import ReportBuilder
from etl_processor.validator import HeaderValidationError
from tests.conftest import ROOT, SAMPLE_DIR, expected_counts, read_csv


def row(day, category, region, amount):
    return {"transaction_date": date(2026, 10, day), "product_category": category,
            "region": region, "transaction_amount": Decimal(amount)}


def test_report_numbers(sales_schema):
    rb = ReportBuilder(sales_schema)
    rb.add_accepted(row(2, "Books", "North", "10.00"))
    rb.add_accepted(row(1, "Books", "South", "20.50"))
    rb.add_accepted(row(1, "Toys", "North", "5.25"))
    rb.add_rejected(["negative_quantity"])
    rb.add_rejected(["missing_transaction_id", "invalid_unit_price"])
    report = rb.build(job_id="abc", status="SUCCEEDED")

    assert report["job_id"] == "abc"
    assert report["counts"] == {"total": 5, "accepted": 3, "rejected": 2}
    assert report["acceptance_rate"] == 60.0
    assert report["rejection_reasons"] == {
        "negative_quantity": 1, "missing_transaction_id": 1, "invalid_unit_price": 1}
    assert report["metrics"] == {"total_sales": 35.75, "avg_order_value": 11.92}
    assert report["aggregates"]["daily_sales"] == [
        {"date": "2026-10-01", "total": 25.75, "count": 2},
        {"date": "2026-10-02", "total": 10.0, "count": 1}]
    assert report["aggregates"]["sales_by_category"] == {"Books": 30.5, "Toys": 5.25}
    assert list(report["aggregates"]["sales_by_region"]) == ["South", "North"]
    assert report["rules_applied"] == ["required", "type", "min", "dedup"]


def test_empty_report(sales_schema):
    report = ReportBuilder(sales_schema).build()
    assert report["counts"] == {"total": 0, "accepted": 0, "rejected": 0}
    assert report["acceptance_rate"] == 0.0
    assert report["metrics"] == {"total_sales": 0.0, "avg_order_value": 0.0}


def test_count_metric_and_blank_group():
    schema = parse_schema({
        "dataset": "t",
        "columns": {"c": {"type": "string"}, "v": {"type": "decimal"}},
        "measure": "v", "metrics": {"n": "count"},
        "aggregates": {"by_c": {"type": "group", "column": "c"}}})
    rb = ReportBuilder(schema)
    rb.add_accepted({"c": None, "v": Decimal("1")})
    rb.add_accepted({"c": "x", "v": None})
    assert rb.metrics() == {"n": 2}
    assert rb.aggregates() == {"by_c": {"(blank)": 1.0}}


def run(path, schema):
    accepted, rejected = [], []
    report = process_rows(read_csv(path), schema, accepted.append,
                          lambda values, reasons: rejected.append((values, reasons)))
    return report.build(), accepted, rejected


def test_dirty_small_matches_generator(sales_schema):
    """Report numbers on dirty_small.csv must equal what the generator says it injected."""
    path = SAMPLE_DIR / "dirty_small.csv"
    expected = expected_counts(path)
    report, accepted, rejected = run(path, sales_schema)

    assert report["counts"] == {"total": expected["rows"], "accepted": expected["accepted"],
                                "rejected": expected["rejected"]}
    assert report["rejection_reasons"] == expected["rejection_reasons"]
    assert report["metrics"]["total_sales"] == expected["total_sales"]
    assert len(accepted) == expected["accepted"]
    assert all(len(reasons) == 1 for _, reasons in rejected)
    assert len({r["transaction_id"] for r in accepted}) == len(accepted)
    assert report["acceptance_rate"] == 95.0


def test_clean_small_is_fully_accepted(sales_schema):
    path = SAMPLE_DIR / "clean_small.csv"
    report, accepted, _ = run(path, sales_schema)
    assert report["counts"]["accepted"] == 100
    assert report["acceptance_rate"] == 100.0
    assert report["metrics"]["total_sales"] == expected_counts(path)["total_sales"]
    categories = {r["product_category"] for r in accepted}
    assert categories <= {"Electronics", "Clothing", "Home & Kitchen", "Books", "Sports",
                          "Beauty", "Toys"}


def test_poison_file_fails_header(sales_schema):
    with pytest.raises(HeaderValidationError, match="unit_price"):
        run(SAMPLE_DIR / "poison.csv", sales_schema)


def test_returns_dataset_runs_through_same_pipeline():
    schema = load_dataset_schema("returns", ROOT / "docs" / "demo")
    path = SAMPLE_DIR / "returns_small.csv"
    report, _, _ = run(path, schema)
    assert report["counts"]["accepted"] == 50
    assert report["metrics"]["total_refunds"] == expected_counts(path)["total_refunds"]
    assert set(report["aggregates"]) == {"daily_refunds", "refunds_by_reason"}


def test_pipeline_edge_rows(sales_schema):
    rows = [
        ["transaction_id", "transaction_date", "product_category", "quantity", "unit_price",
         "region"],
        ["A", "2026-10-01", "Books", "1", "1.00", "North"],
        [],
        ["", "", "", "", "", ""],
        ["B", "2026-10-01", "Books", "1"],
        ["A", "2026-10-02", "Toys", "2", "3.00", "South"],
    ]
    accepted, rejected = [], []
    report = process_rows(rows, sales_schema, accepted.append,
                          lambda v, r: rejected.append(r))
    assert [r["transaction_id"] for r in accepted] == ["A"]
    assert rejected[0][0] == "malformed_row"
    assert rejected[1] == ["duplicate_id"]
    assert report.total == 3


@pytest.mark.parametrize("rows", [[], [[]], [["", " "]]])
def test_pipeline_empty_file_is_poison(sales_schema, rows):
    with pytest.raises(HeaderValidationError):
        process_rows(rows, sales_schema, lambda r: None, lambda v, r: None)
