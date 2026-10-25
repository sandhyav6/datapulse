"""Pure validation: header checks, row checks and de-duplication. No AWS imports.

Reason codes are machine-readable so reports can count them:
  missing_<col>      required value is empty
  invalid_<col>      date or decimal that cannot be parsed
  non_integer_<col>  integer column holding something else
  negative_<col>     numeric value below zero where the minimum is >= 0
  below_min_<col>    numeric value below the schema minimum (but not negative)
  malformed_row      wrong number of fields on the CSV line
  duplicate_id       dedup key already seen in this file (keep_first policy)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import Schema

_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

MALFORMED_ROW = "malformed_row"
DUPLICATE_ID = "duplicate_id"


class HeaderValidationError(ValueError):
    """The file cannot be processed at all (missing required columns, empty file...)."""


def normalise_column_name(name: str) -> str:
    """'Transaction ID' / 'transactionId' / ' transaction-id ' -> 'transaction_id'."""
    name = name.replace("﻿", "").strip()
    name = _CAMEL.sub("_", name).lower()
    return _NON_ALNUM.sub("_", name).strip("_")


@dataclass(frozen=True)
class HeaderResult:
    columns: list[str]
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    duplicated: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.duplicated


def validate_header(header: list[str], schema: Schema) -> HeaderResult:
    columns = [normalise_column_name(h) for h in header]
    duplicated = sorted(c for c, n in Counter(columns).items() if n > 1)
    missing = [c for c in schema.required_columns if c not in columns]
    unexpected = [c for c in columns if c not in schema.columns]
    return HeaderResult(columns=columns, missing=missing, unexpected=unexpected,
                        duplicated=duplicated)


def check_header(header: list[str], schema: Schema) -> HeaderResult:
    """validate_header, raising HeaderValidationError if the file must be rejected whole."""
    result = validate_header(header, schema)
    if not result.ok:
        problems = []
        if result.missing:
            problems.append(f"missing required columns: {', '.join(result.missing)}")
        if result.duplicated:
            problems.append(f"duplicated columns: {', '.join(result.duplicated)}")
        raise HeaderValidationError("; ".join(problems))
    return result


def row_from_values(columns: list[str], values: list[str]) -> tuple[dict[str, str], list[str]]:
    """Zip a CSV line with the header; a field-count mismatch is itself a rejection reason."""
    row = {c: (values[i] if i < len(values) else "") for i, c in enumerate(columns)}
    return row, ([] if len(values) == len(columns) else [MALFORMED_ROW])


# Strict regex fast paths for common formats; strptime (slow: locale lookups on every call)
# remains the fallback for any other format a schema declares.
_FAST_DATES = {
    "%Y-%m-%d": (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), (0, 1, 2)),
    "%d/%m/%Y": (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), (2, 1, 0)),
    "%m/%d/%Y": (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), (2, 0, 1)),
    "%d-%m-%Y": (re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$"), (2, 1, 0)),
    "%Y-%m": (re.compile(r"^(\d{4})-(\d{1,2})$"), (0, 1, None)),  # month -> 1st of month
}


def parse_date(value: str, formats: tuple[str, ...]) -> date | None:
    for fmt in formats:
        fast = _FAST_DATES.get(fmt)
        try:
            if fast:
                match = fast[0].match(value)
                if not match:
                    continue
                parts = match.groups()
                year, month, day = (1 if i is None else int(parts[i]) for i in fast[1])
                return date(year, month, day)
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_integer(value: str) -> int | None:
    return int(value) if _INTEGER.match(value) else None


def parse_decimal(value: str) -> Decimal | None:
    if not _DECIMAL.match(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:  # pragma: no cover - regex already guarantees a valid literal
        return None


def parse_row(row: dict[str, str | None], schema: Schema) -> tuple[list[str], dict[str, Any]]:
    """Validate and type-convert a row in one pass.

    Returns (reasons, values): reasons is empty when the row is valid, and values holds each
    column parsed once (date, int, Decimal or stripped str; None when empty) so the transformer
    never parses the same field twice.
    """
    reasons: list[str] = []
    values: dict[str, Any] = {}
    for name, spec in schema.columns.items():
        value = (row.get(name) or "").strip()
        if not value:
            if spec.required:
                reasons.append(f"missing_{name}")
            values[name] = None
            continue

        parsed: Any = value
        if spec.type == "date":
            parsed = parse_date(value, spec.formats)
            if parsed is None:
                reasons.append(f"invalid_{name}")
        elif spec.type == "integer":
            parsed = parse_integer(value)
            if parsed is None:
                reasons.append(f"non_integer_{name}")
        elif spec.type == "decimal":
            parsed = parse_decimal(value)
            if parsed is None:
                reasons.append(f"invalid_{name}")
        values[name] = parsed

        if (spec.type in ("integer", "decimal") and parsed is not None
                and spec.min is not None and parsed < spec.min_decimal):
            reasons.append(f"negative_{name}" if parsed < 0 <= spec.min else f"below_min_{name}")
    return reasons, values


def validate_row(row: dict[str, str | None], schema: Schema) -> list[str]:
    """Return every reason the row is invalid; an empty list means the row is valid."""
    return parse_row(row, schema)[0]


class Deduplicator:
    """Tracks dedup-key values of accepted rows in one file.

    The key may span several columns (e.g. source + month). Only rows that already passed
    validation are registered, so with keep_first an invalid first occurrence does not cause a
    later valid row with the same key to be rejected.
    """

    def __init__(self, schema: Schema):
        self.key = schema.dedup_key if schema.dedup_policy == "keep_first" else None
        self._seen: set[tuple[str, ...]] = set()

    def check(self, row: dict[str, Any]) -> str | None:
        """Return 'duplicate_id' if this key was already accepted, else register it.

        The pipeline passes the transformed row, so keys are compared after trimming, case
        normalisation and date parsing ("05/10/2026" and "2026-10-05" are the same date).
        """
        if self.key is None:
            return None
        value = tuple("" if row.get(k) is None else str(row.get(k)).strip() for k in self.key)
        if value in self._seen:
            return DUPLICATE_ID
        self._seen.add(value)
        return None
