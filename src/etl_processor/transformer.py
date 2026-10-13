"""Pure transformation of a validated row into its clean, typed form. No AWS imports."""

from __future__ import annotations

import string
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation
from typing import Any

from .config import Schema
from .validator import parse_row

TWO_PLACES = Decimal("0.01")
DERIVATION_ERROR = "derivation_error"


class TransformError(ValueError):
    """A derived field could not be computed (for example division by zero)."""


_SCALES = {n: Decimal(1).scaleb(-n) for n in range(9)}


def round_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def round_to(value: Decimal, scale: int) -> Decimal:
    """Half-up rounding to `scale` decimal places (2 for money, 4 for e.g. temperatures)."""
    return value.quantize(_SCALES[scale], rounding=ROUND_HALF_UP)


def normalise_label(value: str, mode: str | None) -> str:
    value = " ".join(value.split())  # trim and collapse inner whitespace
    if mode == "title":
        return string.capwords(value)  # "men's wear" -> "Men's Wear" (str.title gives "Men'S")
    if mode == "upper":
        return value.upper()
    if mode == "lower":
        return value.lower()
    return value


def transform_row(row: dict[str, str | None], schema: Schema,
                  parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the typed row (str, date, int, Decimal) in schema column order plus derived fields.

    The row must already be valid; empty optional values become None. Pass the values from
    validator.parse_row as `parsed` to avoid parsing every field a second time.
    """
    if parsed is None:
        parsed = parse_row(row, schema)[1]
    out: dict[str, Any] = {}
    for name, spec in schema.columns.items():
        value = parsed[name]
        if value is None:
            out[name] = None
        elif spec.type == "decimal":
            out[name] = round_to(value, spec.scale)
        elif spec.type == "string":
            out[name] = normalise_label(value, spec.normalise)
        else:
            out[name] = value

    for name, formula in schema.derived.items():
        try:
            out[name] = round_money(formula(out))
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
