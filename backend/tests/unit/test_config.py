import json

import pytest

from etl_processor.config import (
    SchemaError,
    Settings,
    UnknownDatasetError,
    load_dataset_schema,
    parse_schema,
)
from tests.conftest import ROOT, SCHEMA_DIR


def minimal(**overrides):
    raw = {"dataset": "things", "columns": {"id": {"type": "string", "required": True}}}
    raw.update(overrides)
    return raw


def test_sales_schema_loads(sales_schema):
    assert sales_schema.dataset == "sales"
    assert sales_schema.required_columns == [
        "transaction_id", "transaction_date", "product_category", "quantity", "unit_price",
        "region"]
    assert sales_schema.output_columns[-1] == "transaction_amount"
    assert sales_schema.dedup_key == ("transaction_id",)
    assert sales_schema.rules == ["required", "type", "min", "dedup"]
    assert [a.name for a in sales_schema.aggregates] == [
        "daily_sales", "sales_by_category", "sales_by_region"]


def test_demo_returns_schema_is_valid():
    """The second dataset proves the pipeline is config-driven, not hardcoded to sales."""
    schema = load_dataset_schema("returns", ROOT / "demo")
    assert schema.measure == "refund_amount"
    assert set(schema.metrics) == {"total_refunds", "avg_refund"}


@pytest.mark.parametrize("bad", [
    {"dataset": "Bad-Name"},
    {"columns": {}},
    {"columns": {"id": {"type": "uuid"}}},
    {"columns": {"id": {"type": "string", "normalise": "camel"}}},
    {"dedup_key": "nope", "dedup_policy": "keep_first"},
    {"dedup_policy": "keep_last", "dedup_key": "id"},
    {"derived": {"x": "id * 2"}},
    {"derived": {"x": "__import__('os')"}},
    {"derived": {"id": "1"}},
    {"measure": "id"},
    {"metrics": {"m": "median"}},
    {"aggregates": {"a": {"type": "group", "column": "id"}}},
    {"aggregates": {"a": {"type": "pivot", "column": "id"}}},
])
def test_invalid_schemas_rejected(bad):
    with pytest.raises(SchemaError):
        parse_schema(minimal(**bad))


def test_daily_aggregate_needs_date_column():
    raw = minimal(columns={"id": {"type": "string"}, "n": {"type": "integer"}},
                  measure="n", aggregates={"a": {"type": "daily", "column": "id"}})
    with pytest.raises(SchemaError):
        parse_schema(raw)


@pytest.mark.parametrize("name", ["unknown", "../sales", "Sales", ""])
def test_unknown_or_unsafe_dataset(name):
    with pytest.raises(UnknownDatasetError):
        load_dataset_schema(name, SCHEMA_DIR)


def test_schema_file_must_match_dataset_name(tmp_path):
    (tmp_path / "foo.json").write_text(json.dumps(minimal(dataset="bar")))
    with pytest.raises(SchemaError):
        load_dataset_schema("foo", tmp_path)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BUCKET_NAME", "b")
    monkeypatch.setenv("TABLE_NAME", "t")
    monkeypatch.delenv("SCHEMA_DIR", raising=False)
    monkeypatch.delenv("OUTPUT_FORMAT", raising=False)
    settings = Settings.from_env()
    assert settings.output_format == "csv"
    assert (settings.schema_dir / "sales.json").is_file()
    monkeypatch.setenv("OUTPUT_FORMAT", "xml")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_public_dataset_schemas_load():
    temp = load_dataset_schema("global_temperature", SCHEMA_DIR)
    assert temp.load_type == "snapshot"
    assert temp.dedup_key == ("source", "year")
    assert temp.columns["mean"].scale == 4 and temp.measure_scale == 4
    assert temp.columns["year"].formats == ("%Y-%m",)
    pop = load_dataset_schema("world_population", SCHEMA_DIR)
    assert pop.measure_scale == 0
    world = next(a for a in pop.aggregates if a.name == "world_population_by_year")
    assert (world.type, world.func, world.where) == ("yearly", "sum", (("country_code", "WLD"),))


TWO_COLS = {"k": {"type": "string"}, "d": {"type": "date"}, "n": {"type": "decimal"},
            "y": {"type": "integer"}}


@pytest.mark.parametrize("bad, message", [
    ({"columns": {"n": {"type": "decimal", "scale": 9}}}, "scale"),
    ({"columns": TWO_COLS, "dedup_key": ["k", "zz"]}, "dedup_key"),
    ({"columns": TWO_COLS, "load_type": "streaming"}, "load_type"),
    ({"columns": TWO_COLS, "measure": "n",
      "aggregates": {"a": {"type": "group", "column": "k", "func": "mean"}}}, "incremental"),
    ({"columns": TWO_COLS, "measure": "n", "load_type": "snapshot",
      "aggregates": {"a": {"type": "yearly", "column": "k"}}}, "yearly"),
    ({"columns": TWO_COLS, "measure": "n", "load_type": "snapshot",
      "aggregates": {"a": {"type": "daily", "column": "d", "func": "max"}}}, "func"),
    ({"columns": TWO_COLS, "measure": "n", "load_type": "snapshot",
      "aggregates": {"a": {"type": "group", "column": "k", "func": "median"}}}, "func"),
    ({"columns": TWO_COLS, "measure": "n",
      "aggregates": {"a": {"type": "group", "column": "k", "where": {"zz": 1}}}}, "where"),
    ({"columns": TWO_COLS, "measure": "n",
      "aggregates": {"a": {"type": "group", "column": "k", "exclude": {"k": "X"}}}}, "exclude"),
])
def test_new_schema_rules(bad, message):
    with pytest.raises(SchemaError, match=message):
        parse_schema(minimal(**bad))


def test_snapshot_allows_any_function_and_integer_years():
    schema = parse_schema(minimal(columns=TWO_COLS, measure="n", load_type="snapshot",
                                  aggregates={"a": {"type": "yearly", "column": "y",
                                                    "func": "max"}}))
    assert schema.aggregates[0].func == "max"
