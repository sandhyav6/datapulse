"""Pure single-pass report building. No AWS imports.

Memory is constant in the number of rows except for the aggregates, which grow with the number
of distinct dates/categories, not with the number of rows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from .config import Schema
from .transformer import round_money

BLANK = "(blank)"


def _money(value: Decimal) -> float:
    return float(round_money(value))


class ReportBuilder:
    def __init__(self, schema: Schema):
        self.schema = schema
        self.accepted = 0
        self.rejected = 0
        self.reasons: Counter[str] = Counter()
        self.measure_total = Decimal(0)
        self.measure_count = 0
        self._daily: dict[str, dict[date, list]] = {}
        self._groups: dict[str, dict[str, Decimal]] = {}
        for agg in schema.aggregates:
            if agg.type == "daily":
                self._daily[agg.name] = defaultdict(lambda: [Decimal(0), 0])
            else:
                self._groups[agg.name] = defaultdict(Decimal)

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    def add_accepted(self, row: dict[str, Any]) -> None:
        self.accepted += 1
        measure = row.get(self.schema.measure) if self.schema.measure else None
        if measure is None:
            return
        self.measure_total += measure
        self.measure_count += 1
        for agg in self.schema.aggregates:
            key = row.get(agg.column)
            if agg.type == "daily":
                if key is not None:
                    bucket = self._daily[agg.name][key]
                    bucket[0] += measure
                    bucket[1] += 1
            else:
                self._groups[agg.name][key if key is not None else BLANK] += measure

    def add_rejected(self, reasons: list[str]) -> None:
        self.rejected += 1
        self.reasons.update(reasons)

    def metrics(self) -> dict[str, float | int]:
        out: dict[str, float | int] = {}
        for name, func in self.schema.metrics.items():
            if func == "sum":
                out[name] = _money(self.measure_total)
            elif func == "mean":
                mean = self.measure_total / self.measure_count if self.measure_count else 0
                out[name] = _money(Decimal(mean))
            else:
                out[name] = self.accepted
        return out

    def aggregates(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for agg in self.schema.aggregates:
            if agg.type == "daily":
                out[agg.name] = [
                    {"date": day.isoformat(), "total": _money(total), "count": count}
                    for day, (total, count) in sorted(self._daily[agg.name].items())
                ]
            else:
                groups = self._groups[agg.name]
                out[agg.name] = {
                    key: _money(total)
                    for key, total in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))
                }
        return out

    def acceptance_rate(self) -> float:
        return round(100 * self.accepted / self.total, 2) if self.total else 0.0

    def build(self, **meta: Any) -> dict[str, Any]:
        """Assemble the report; meta (job_id, source, timings, outputs...) goes first."""
        report = dict(meta)
        report["dataset"] = self.schema.dataset
        report["counts"] = {"total": self.total, "accepted": self.accepted,
                            "rejected": self.rejected}
        report["acceptance_rate"] = self.acceptance_rate()
        report["rejection_reasons"] = dict(self.reasons.most_common())
        report["metrics"] = self.metrics()
        report["aggregates"] = self.aggregates()
        report["rules_applied"] = self.schema.rules
        return report
