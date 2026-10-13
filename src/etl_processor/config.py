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
METRIC_FUNCS = {"sum", "mean", "count"}
AGGREGATE_TYPES = {"daily", "group"}
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

    @cached_property
    def min_decimal(self) -> Decimal | None:
        return None if self.min is None else Decimal(str(self.min))


@dataclass(frozen=True)
class AggregateSpec:
    name: str
    type: str  # "daily" (list ordered by date) or "group" (mapping value -> total)
    column: str


@dataclass(frozen=True)
class Schema:
    dataset: str
    columns: dict[str, ColumnSpec]
    dedup_key: str | None = None
    dedup_policy: str = "none"
    derived: dict[str, Callable[[dict], Decimal]] = field(default_factory=dict)
    derived_source: dict[str, str] = field(default_factory=dict)
    measure: str | None = None
    metrics: dict[str, str] = field(default_factory=dict)
    aggregates: tuple[AggregateSpec, ...] = ()

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
        columns[name] = ColumnSpec(
            name=name,
            type=ctype,
            required=bool(spec.get("required", False)),
            formats=formats,
            normalise=spec.get("normalise"),
            min=spec.get("min"),
        )

    dedup_key = raw.get("dedup_key")
    dedup_policy = raw.get("dedup_policy", "keep_first" if dedup_key else "none")
    if dedup_policy not in DEDUP_POLICIES:
        raise SchemaError(f"unknown dedup_policy {dedup_policy!r}")
    if dedup_policy != "none" and dedup_key not in columns:
        raise SchemaError(f"dedup_key {dedup_key!r} is not a column")

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
        if spec.get("type") not in AGGREGATE_TYPES:
            raise SchemaError(f"aggregate {name!r}: unknown type {spec.get('type')!r}")
        if spec.get("column") not in columns:
            raise SchemaError(f"aggregate {name!r}: unknown column {spec.get('column')!r}")
        if spec["type"] == "daily" and columns[spec["column"]].type != "date":
            raise SchemaError(f"aggregate {name!r}: daily aggregates need a date column")
        if measure is None:
            raise SchemaError(f"aggregate {name!r} needs a 'measure'")
        aggregates.append(AggregateSpec(name=name, type=spec["type"], column=spec["column"]))

    return Schema(
        dataset=dataset,
        columns=columns,
        dedup_key=dedup_key if dedup_policy != "none" else None,
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
