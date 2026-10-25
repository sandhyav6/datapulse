from datetime import date
from decimal import Decimal

import pytest

from etl_processor.config import parse_schema
from etl_processor.transformer import (
    TransformError,
    normalise_label,
    round_money,
    serialise_row,
    transform_row,
)

ROW = {"transaction_id": "  TXN-9 ", "transaction_date": "05/10/2026",
       "product_category": "  home   &  KITCHEN ", "quantity": " 3 ", "unit_price": "19.995",
       "region": "north"}


def test_full_transformation(sales_schema):
    out = transform_row(ROW, sales_schema)
    assert out == {
        "transaction_id": "TXN-9",
        "transaction_date": date(2026, 10, 5),
        "product_category": "Home & Kitchen",
        "quantity": 3,
        "unit_price": Decimal("20.00"),
        "region": "North",
        "transaction_amount": Decimal("60.00"),
    }
    assert list(out) == sales_schema.output_columns


def test_iso_date_kept(sales_schema):
    out = transform_row(dict(ROW, transaction_date="2026-10-05"), sales_schema)
    assert out["transaction_date"].isoformat() == "2026-10-05"


@pytest.mark.parametrize("value, expected", [
    ("2.345", "2.35"), ("2.344", "2.34"), ("0.005", "0.01"), ("1", "1.00"), ("2.5", "2.50")])
def test_round_half_up(value, expected):
    assert str(round_money(Decimal(value))) == expected


def test_amount_uses_decimal_not_float(sales_schema):
    out = transform_row(dict(ROW, quantity="3", unit_price="0.10"), sales_schema)
    assert out["transaction_amount"] == Decimal("0.30")  # float would give 0.30000000000000004


@pytest.mark.parametrize("value, mode, expected", [
    ("men's  wear", "title", "Men's Wear"),
    ("  ELECTRONICS ", "title", "Electronics"),
    ("Damaged Item", "lower", "damaged item"),
    ("north", "upper", "NORTH"),
    ("  As  Is ", None, "As Is"),
])
def test_normalise_label(value, mode, expected):
    assert normalise_label(value, mode) == expected


def test_serialise_row(sales_schema):
    assert serialise_row(transform_row(ROW, sales_schema)) == [
        "TXN-9", "2026-10-05", "Home & Kitchen", "3", "20.00", "North", "60.00"]


def test_optional_empty_value_becomes_none():
    schema = parse_schema({"dataset": "t", "columns": {
        "id": {"type": "string", "required": True}, "note": {"type": "string"}}})
    out = transform_row({"id": "1", "note": " "}, schema)
    assert out == {"id": "1", "note": None}
    assert serialise_row(out) == ["1", ""]


def test_division_by_zero_raises_transform_error():
    schema = parse_schema({"dataset": "t", "columns": {
        "a": {"type": "decimal"}, "b": {"type": "decimal"}}, "derived": {"r": "a / b"}})
    assert transform_row({"a": "1", "b": "4"}, schema)["r"] == Decimal("0.25")
    with pytest.raises(TransformError):
        transform_row({"a": "1", "b": "0"}, schema)


def test_expression_with_constants_and_unary():
    schema = parse_schema({"dataset": "t", "columns": {"a": {"type": "integer"}},
                           "derived": {"r": "-(a + 1) * 1.5"}})
    assert transform_row({"a": "3"}, schema)["r"] == Decimal("-6.00")
