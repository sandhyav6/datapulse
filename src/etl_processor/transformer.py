"""Pure transformation of a validated row into its clean, typed form. No AWS imports."""

from __future__ import annotations

import ast
import re
import string
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation
from typing import Any

from .config import Schema
from .validator import parse_date, parse_decimal, parse_integer

TWO_PLACES = Decimal("0.01")
_SPACES = re.compile(r"\s+")
_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}

DERIVATION_ERROR = "derivation_error"


class TransformError(ValueError):
    """A derived field could not be computed (for example division by zero)."""


def round_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def normalise_label(value: str, mode: str | None) -> str:
    value = _SPACES.sub(" ", value.strip())
    if mode == "title":
        return string.capwords(value)  # "men's wear" -> "Men's Wear" (str.title gives "Men'S")
    if mode == "upper":
        return value.upper()
    if mode == "lower":
        return value.lower()
    return value


def _evaluate(node: ast.AST, row: dict[str, Any]) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, row)
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        return Decimal(row[node.id])
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, row)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_evaluate(node.left, row), _evaluate(node.right, row))
    raise TransformError(f"unsupported expression node {type(node).__name__}")


def transform_row(row: dict[str, str | None], schema: Schema) -> dict[str, Any]:
    """Return the typed row (str, date, int, Decimal) in schema column order plus derived fields.

    The row must already have passed validate_row; empty optional values become None.
    """
    out: dict[str, Any] = {}
    for name, spec in schema.columns.items():
        value = (row.get(name) or "").strip()
        if not value:
            out[name] = None
        elif spec.type == "date":
            out[name] = parse_date(value, spec.formats)
        elif spec.type == "integer":
            out[name] = parse_integer(value)
        elif spec.type == "decimal":
            out[name] = round_money(parse_decimal(value))
        else:
            out[name] = normalise_label(value, spec.normalise)

    for name, expression in schema.derived.items():
        try:
            out[name] = round_money(_evaluate(expression, out))
        except (TypeError, DivisionByZero, InvalidOperation, ZeroDivisionError) as exc:
            raise TransformError(f"cannot compute {name}: {exc}") from exc
    return out


def serialise_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def serialise_row(row: dict[str, Any]) -> list[str]:
    return [serialise_value(v) for v in row.values()]
