"""Pure row pipeline: header check -> validate -> transform -> dedup -> report. No AWS imports.

The caller supplies the CSV rows (any iterable of lists of strings) and two sinks, so the same
code runs against a local file in tests and a streaming S3 body in Lambda.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .config import Schema
from .reporter import ReportBuilder
from .transformer import DERIVATION_ERROR, TransformError, transform_row
from .validator import (
    Deduplicator,
    HeaderResult,
    HeaderValidationError,
    check_header,
    parse_row,
    row_from_values,
)

AcceptSink = Callable[[dict[str, Any]], None]
RejectSink = Callable[[list[str], list[str]], None]


def process_rows(
    rows: Iterable[list[str]],
    schema: Schema,
    accept: AcceptSink,
    reject: RejectSink,
    on_header: Callable[[list[str], HeaderResult], None] | None = None,
) -> ReportBuilder:
    """Run every row through the pipeline, sending clean rows to accept and bad rows (original
    values plus reason codes) to reject. Raises HeaderValidationError for a poison file."""
    it = iter(rows)
    header = next(it, None)
    if not header or not any(h.strip() for h in header):
        raise HeaderValidationError("file is empty or has no header row")
    header_result = check_header(header, schema)
    if on_header:
        on_header(header, header_result)

    report = ReportBuilder(schema)
    dedup = Deduplicator(schema)
    for values in it:
        if not any(v.strip() for v in values):
            continue  # blank line
        raw, reasons = row_from_values(header_result.columns, values)
        row_reasons, parsed = parse_row(raw, schema)
        reasons += row_reasons
        clean = None
        if not reasons:
            try:
                clean = transform_row(raw, schema, parsed)
            except TransformError:
                reasons.append(DERIVATION_ERROR)
        if not reasons:
            duplicate = dedup.check(clean)  # compare cleaned values: " a " == "A" after normalising
            if duplicate:
                reasons.append(duplicate)

        if reasons:
            report.add_rejected(reasons)
            reject(values, reasons)
        else:
            report.add_accepted(clean)
            accept(clean)
    return report
