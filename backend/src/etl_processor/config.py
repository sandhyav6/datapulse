"""Environment settings and dataset schema loading.

A schema is a JSON file in SCHEMA_DIR named <dataset>.json. Loading it validates the whole
file up front so a bad schema fails loudly at load time, not halfway through a CSV.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from functools import cached_property
from pathlib import Path

COLUMN_TYPES = {"string", "date", "integer", "decimal"}
NORMALISERS = {"title", "upper", "lower"}
DEDUP_POLICIES = {"keep_first", "none"}
METRIC_FUNCS = {"sum", "mean", "min", "max", "count"}
AGGREGATE_TYPES = {"daily", "group", "yearly"}
AGGREGATE_FUNCS = {"sum", "mean", "min", "max", "count"}
# Incremental datasets (each file adds new rows) are combined across files by summing, which
# is only correct for additive functions. Snapshot datasets (each file replaces the last) are
# read from the latest job only, so any function is fine.
LOAD_TYPES = {"incremental", "snapshot"}
ADDITIVE_FUNCS = {"sum", "count"}
DATASET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class SchemaError(ValueError):
    """The schema file itself is invalid."""


class UnknownDatasetError(LookupError):
    """No schema exists for the dataset named in the S3 key."""


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    required: bool = False
    formats: tuple[str, ...] = ()
    normalise: str | None = None
    min: float | None = None
    scale: int = 2  # decimal places kept for decimal columns (half-up rounding)

    @cached_property
    def min_decimal(self) -> Decimal | None:
        return None if self.min is None else Decimal(str(self.min))


@dataclass(frozen=True)
class AggregateSpec:
    """daily: list of {date, total, count}; group: {value: result} sorted by result;
    yearly: {year: result} in year order. `where` keeps only rows whose column equals a value;
    `exclude` drops rows whose column is in a set (e.g. World Bank regional aggregates)."""

    name: str
    type: str
    column: str
    func: str = "sum"
    where: tuple[tuple[str, str], ...] = ()
    exclude: tuple[tuple[str, frozenset[str]], ...] = ()


@dataclass(frozen=True)
class Schema:
    dataset: str
    columns: dict[str, ColumnSpec]
    dedup_key: tuple[str, ...] | None = None
    dedup_policy: str = "none"
    derived: dict[str, Callable[[dict], Decimal]] = field(default_factory=dict)
    derived_source: dict[str, str] = field(default_factory=dict)
    measure: str | None = None
    metrics: dict[str, str] = field(default_factory=dict)
    aggregates: tuple[AggregateSpec, ...] = ()
    load_type: str = "incremental"

    @property
    def measure_scale(self) -> int:
        """Decimal places for measure results: the column's scale, 2 for derived fields."""
        spec = self.columns.get(self.measure or "")
        if spec is None:
            return 2
        return spec.scale if spec.type == "decimal" else 0

    @property
    def required_columns(self) -> list[str]:
        return [c.name for c in self.columns.values() if c.required]

    @property
    def output_columns(self) -> list[str]:
        return list(self.columns) + list(self.derived)

    @property
    def rules(self) -> list[str]:
        rules = ["required", "type"]
        if any(c.min is not None for c in self.columns.values()):
            rules.append("min")
        if self.dedup_policy != "none":
            rules.append("dedup")
        return rules


@dataclass(frozen=True)
class Settings:
    bucket_name: str
    table_name: str
    schema_dir: Path
    output_format: str = "csv"

    @classmethod
    def from_env(cls) -> Settings:
        schema_dir = os.environ.get("SCHEMA_DIR") or str(DEFAULT_SCHEMA_DIR)
        output_format = os.environ.get("OUTPUT_FORMAT", "csv").lower()
        if output_format not in {"csv", "parquet", "both"}:
            raise ValueError(f"OUTPUT_FORMAT must be csv, parquet or both, got {output_format!r}")
        return cls(
            bucket_name=os.environ["BUCKET_NAME"],
            table_name=os.environ["TABLE_NAME"],
            schema_dir=Path(schema_dir),
            output_format=output_format,
        )


_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


def _build(node: ast.AST) -> Callable[[dict], Decimal]:
    """Turn a validated formula AST into nested closures, built once per schema load."""
    if isinstance(node, ast.Expression):
        return _build(node.body)
    if isinstance(node, ast.Constant):
        constant = Decimal(str(node.value))
        return lambda row: constant
    if isinstance(node, ast.Name):
        column = node.id
        return lambda row: Decimal(row[column])
    if isinstance(node, ast.UnaryOp):
        operand = _build(node.operand)
        if isinstance(node.op, ast.USub):
            return lambda row: -operand(row)
        return operand
    op, left, right = _BINOPS[type(node.op)], _build(node.left), _build(node.right)
    return lambda row: op(left(row), right(row))


def _compile_expression(name: str, source: str, numeric: set[str]) -> Callable[[dict], Decimal]:
    """Compile a derived-field formula, allowing only arithmetic on numeric columns."""
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise SchemaError(f"derived field {name!r}: invalid expression {source!r}") from exc
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise SchemaError(f"derived field {name!r}: {type(node).__name__} not allowed")
        if isinstance(node, ast.Name) and node.id not in numeric:
            raise SchemaError(f"derived field {name!r}: {node.id!r} is not a numeric column")
        if isinstance(node, ast.Constant) and not isinstance(node.value, int | float):
            raise SchemaError(f"derived field {name!r}: only numeric constants allowed")
    return _build(tree)


def parse_schema(raw: dict) -> Schema:
    dataset = raw.get("dataset")
    if not isinstance(dataset, str) or not DATASET_NAME.match(dataset):
        raise SchemaError(f"invalid dataset name {dataset!r}")
    raw_columns = raw.get("columns")
    if not isinstance(raw_columns, dict) or not raw_columns:
        raise SchemaError("schema needs a non-empty 'columns' object")

    columns: dict[str, ColumnSpec] = {}
    for name, spec in raw_columns.items():
        ctype = spec.get("type")
        if ctype not in COLUMN_TYPES:
            raise SchemaError(f"column {name!r}: unknown type {ctype!r}")
        if spec.get("normalise") not in (None, *NORMALISERS):
            raise SchemaError(f"column {name!r}: unknown normaliser {spec['normalise']!r}")
        formats = tuple(spec.get("formats", ("%Y-%m-%d",) if ctype == "date" else ()))
        scale = spec.get("scale", 2)
        if not isinstance(scale, int) or not 0 <= scale <= 8:
            raise SchemaError(f"column {name!r}: scale must be an integer 0-8")
        columns[name] = ColumnSpec(
            name=name,
            type=ctype,
            required=bool(spec.get("required", False)),
            formats=formats,
            normalise=spec.get("normalise"),
            min=spec.get("min"),
            scale=scale,
        )

    raw_key = raw.get("dedup_key")
    dedup_key = (raw_key,) if isinstance(raw_key, str) else tuple(raw_key or ())
    dedup_policy = raw.get("dedup_policy", "keep_first" if dedup_key else "none")
    if dedup_policy not in DEDUP_POLICIES:
        raise SchemaError(f"unknown dedup_policy {dedup_policy!r}")
    if dedup_policy != "none" and (not dedup_key or any(k not in columns for k in dedup_key)):
        raise SchemaError(f"dedup_key {raw_key!r} must name existing columns")

    load_type = raw.get("load_type", "incremental")
    if load_type not in LOAD_TYPES:
        raise SchemaError(f"unknown load_type {load_type!r}")

    numeric = {n for n, c in columns.items() if c.type in ("integer", "decimal")}
    derived: dict[str, Callable[[dict], Decimal]] = {}
    derived_source: dict[str, str] = {}
    for name, source in raw.get("derived", {}).items():
        if name in columns:
            raise SchemaError(f"derived field {name!r} clashes with a column")
        derived[name] = _compile_expression(name, source, numeric)
        derived_source[name] = source
        numeric.add(name)

    measure = raw.get("measure")
    if measure is not None and measure not in numeric:
        raise SchemaError(f"measure {measure!r} must be a numeric column or derived field")
    metrics = raw.get("metrics", {})
    for name, func in metrics.items():
        if func not in METRIC_FUNCS:
            raise SchemaError(f"metric {name!r}: unknown function {func!r}")
        if measure is None and func != "count":
            raise SchemaError(f"metric {name!r} needs a 'measure'")

    aggregates = []
    for name, spec in raw.get("aggregates", {}).items():
        atype, column, func = spec.get("type"), spec.get("column"), spec.get("func", "sum")
        if atype not in AGGREGATE_TYPES:
            raise SchemaError(f"aggregate {name!r}: unknown type {atype!r}")
        if column not in columns:
            raise SchemaError(f"aggregate {name!r}: unknown column {column!r}")
        if atype == "daily" and columns[column].type != "date":
            raise SchemaError(f"aggregate {name!r}: daily aggregates need a date column")
        if atype == "yearly" and columns[column].type not in ("date", "integer"):
            raise SchemaError(f"aggregate {name!r}: yearly aggregates need a date or integer "
                              "(year) column")
        if func not in AGGREGATE_FUNCS or (atype == "daily" and func != "sum"):
            raise SchemaError(f"aggregate {name!r}: unsupported func {func!r}")
        if load_type == "incremental" and func not in ADDITIVE_FUNCS:
            raise SchemaError(f"aggregate {name!r}: incremental datasets are summed across "
                              f"files, so func must be sum or count, not {func!r}")
        where, exclude = spec.get("where", {}), spec.get("exclude", {})
        if any(c not in columns for c in where):
            raise SchemaError(f"aggregate {name!r}: 'where' names an unknown column")
        if any(c not in columns or not isinstance(v, list) for c, v in exclude.items()):
            raise SchemaError(f"aggregate {name!r}: 'exclude' maps known columns to lists")
        if measure is None:
            raise SchemaError(f"aggregate {name!r} needs a 'measure'")
        aggregates.append(AggregateSpec(
            name=name, type=atype, column=column, func=func,
            where=tuple((c, str(v)) for c, v in where.items()),
            exclude=tuple((c, frozenset(str(x) for x in v)) for c, v in exclude.items())))

    return Schema(
        dataset=dataset,
        columns=columns,
        dedup_key=dedup_key if dedup_policy != "none" else None,
        load_type=load_type,
        dedup_policy=dedup_policy,
        derived=derived,
        derived_source=derived_source,
        measure=measure,
        metrics=dict(metrics),
        aggregates=tuple(aggregates),
    )


def load_schema(path: str | Path) -> Schema:
    with open(path, encoding="utf-8") as fh:
        return parse_schema(json.load(fh))


def load_dataset_schema(dataset: str, schema_dir: str | Path = DEFAULT_SCHEMA_DIR) -> Schema:
    """Load <schema_dir>/<dataset>.json, refusing names that could escape the directory."""
    if not DATASET_NAME.match(dataset):
        raise UnknownDatasetError(f"invalid dataset name {dataset!r}")
    path = Path(schema_dir) / f"{dataset}.json"
    if not path.is_file():
        raise UnknownDatasetError(f"no schema for dataset {dataset!r}")
    schema = load_schema(path)
    if schema.dataset != dataset:
        raise SchemaError(f"{path.name} declares dataset {schema.dataset!r}")
    return schema
