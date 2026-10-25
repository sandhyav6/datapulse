import pytest

from etl_processor.validator import (
    Deduplicator,
    HeaderValidationError,
    check_header,
    normalise_column_name,
    row_from_values,
    validate_header,
    validate_row,
)

GOOD = {"transaction_id": "TXN-1", "transaction_date": "2026-10-10",
        "product_category": "Books", "quantity": "2", "unit_price": "9.99", "region": "North"}


def with_(**changes):
    row = dict(GOOD)
    row.update(changes)
    return row


@pytest.mark.parametrize("raw, expected", [
    ("transaction_id", "transaction_id"),
    ("Transaction ID", "transaction_id"),
    (" transaction-id ", "transaction_id"),
    ("transactionId", "transaction_id"),
    ("﻿Transaction_Date", "transaction_date"),
    ("Unit Price ($)", "unit_price"),
])
def test_normalise_column_name(raw, expected):
    assert normalise_column_name(raw) == expected


def test_header_ok_with_messy_names_and_extra_column(sales_schema):
    header = ["Transaction ID", "Transaction Date", "Product Category", "Quantity",
              "Unit Price", "Region", "Store"]
    result = validate_header(header, sales_schema)
    assert result.ok
    assert result.missing == []
    assert result.unexpected == ["store"]


def test_header_missing_required_fails_file(sales_schema):
    header = ["transaction_id", "transaction_date", "product_category", "quantity", "region"]
    assert validate_header(header, sales_schema).missing == ["unit_price"]
    with pytest.raises(HeaderValidationError, match="unit_price"):
        check_header(header, sales_schema)


def test_header_duplicate_column_fails_file(sales_schema):
    header = list(GOOD) + ["Region"]
    with pytest.raises(HeaderValidationError, match="duplicated"):
        check_header(header, sales_schema)


def test_valid_row_has_no_reasons(sales_schema):
    assert validate_row(GOOD, sales_schema) == []


@pytest.mark.parametrize("changes, reason", [
    ({"transaction_id": ""}, "missing_transaction_id"),
    ({"transaction_id": "   "}, "missing_transaction_id"),
    ({"transaction_date": ""}, "missing_transaction_date"),
    ({"product_category": ""}, "missing_product_category"),
    ({"quantity": ""}, "missing_quantity"),
    ({"unit_price": ""}, "missing_unit_price"),
    ({"region": None}, "missing_region"),
    ({"transaction_date": "2026-13-45"}, "invalid_transaction_date"),
    ({"transaction_date": "31/02/2026"}, "invalid_transaction_date"),
    ({"transaction_date": "2026/10/01"}, "invalid_transaction_date"),
    ({"quantity": "2.5"}, "non_integer_quantity"),
    ({"quantity": "two"}, "non_integer_quantity"),
    ({"quantity": "1_000"}, "non_integer_quantity"),
    ({"quantity": "-3"}, "negative_quantity"),
    ({"quantity": "0"}, "below_min_quantity"),
    ({"unit_price": "abc"}, "invalid_unit_price"),
    ({"unit_price": "12.3.4"}, "invalid_unit_price"),
    ({"unit_price": "NaN"}, "invalid_unit_price"),
    ({"unit_price": "1e3"}, "invalid_unit_price"),
    ({"unit_price": "$19.99"}, "invalid_unit_price"),
    ({"unit_price": "-0.01"}, "negative_unit_price"),
])
def test_each_reason_code(sales_schema, changes, reason):
    assert validate_row(with_(**changes), sales_schema) == [reason]


def test_alternate_date_format_and_whitespace_are_valid(sales_schema):
    row = with_(transaction_date=" 10/10/2026 ", quantity=" +4 ", unit_price=" .50 ")
    assert validate_row(row, sales_schema) == []


def test_multiple_reasons_reported(sales_schema):
    row = with_(transaction_id="", quantity="-1", unit_price="x")
    assert validate_row(row, sales_schema) == [
        "missing_transaction_id", "negative_quantity", "invalid_unit_price"]


def test_row_from_values_flags_wrong_field_count():
    cols = ["a", "b", "c"]
    assert row_from_values(cols, ["1", "2", "3"]) == ({"a": "1", "b": "2", "c": "3"}, [])
    row, reasons = row_from_values(cols, ["1", "2"])
    assert row == {"a": "1", "b": "2", "c": ""} and reasons == ["malformed_row"]
    assert row_from_values(cols, ["1", "2", "3", "4"])[1] == ["malformed_row"]


def test_dedup_keep_first(sales_schema):
    dedup = Deduplicator(sales_schema)
    assert dedup.check(GOOD) is None
    assert dedup.check(with_(region="South")) == "duplicate_id"
    assert dedup.check(with_(transaction_id=" TXN-1 ")) == "duplicate_id"
    assert dedup.check(with_(transaction_id="TXN-2")) is None


def test_dedup_policy_none(sales_schema):
    from dataclasses import replace
    dedup = Deduplicator(replace(sales_schema, dedup_policy="none"))
    assert dedup.check(GOOD) is None
    assert dedup.check(GOOD) is None


def test_month_only_dates():
    from datetime import date

    from etl_processor.validator import parse_date
    assert parse_date("1850-01", ("%Y-%m",)) == date(1850, 1, 1)
    assert parse_date("2024-13", ("%Y-%m",)) is None
    assert parse_date("2024-01-05", ("%Y-%m",)) is None


def test_composite_dedup_on_cleaned_values():
    from etl_processor.config import parse_schema
    schema = parse_schema({"dataset": "t", "columns": {"a": {"type": "string"},
                                                       "b": {"type": "string"}},
                           "dedup_key": ["a", "b"]})
    dedup = Deduplicator(schema)
    assert dedup.check({"a": "X", "b": "1"}) is None
    assert dedup.check({"a": "X", "b": "2"}) is None
    assert dedup.check({"a": " X ", "b": "1"}) == "duplicate_id"
