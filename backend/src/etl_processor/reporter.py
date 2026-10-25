"""Pure single-pass report building. No AWS imports.

Memory is constant in the number of rows except for the aggregates, which grow with the number
of distinct dates/years/categories, not with the number of rows.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any

from .config import AggregateSpec, Schema
from .transformer import round_to

BLANK = "(blank)"


class _Stat:
    """Running sum / count / min / max for one aggregate bucket."""

    __slots__ = ("total", "count", "low", "high")

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.count = 0
        self.low: Decimal | None = None
        self.high: Decimal | None = None

    def add(self, value: Decimal) -> None:
        self.total += value
        self.count += 1
        if self.low is None or value < self.low:
            self.low = value
        if self.high is None or value > self.high:
            self.high = value

    def result(self, func: str) -> Decimal | int:
        if func == "count":
            return self.count
        if func == "mean":
            return self.total / self.count if self.count else Decimal(0)
        if func == "min":
            return self.low if self.low is not None else Decimal(0)
        if func == "max":
            return self.high if self.high is not None else Decimal(0)
        return self.total


def _bucket_key(agg: AggregateSpec, value: Any) -> Any:
    if agg.type == "yearly":
        return value.year if isinstance(value, date) else value
    if agg.type == "daily":
        return value
    return value if value is not None else BLANK


class ReportBuilder:
    def __init__(self, schema: Schema):
        self.schema = schema
        self.accepted = 0
        self.rejected = 0
        self.reasons: Counter[str] = Counter()
        self.overall = _Stat()
        self._buckets: dict[str, dict[Any, _Stat]] = {a.name: {} for a in schema.aggregates}
        scale = schema.measure_scale
        self._scale = scale
        self._mean_scale = max(scale, 2)

    @property
    def total(self) -> int:
        return self.accepted + self.rejected

    def add_accepted(self, row: dict[str, Any]) -> None:
        self.accepted += 1
        measure = row.get(self.schema.measure) if self.schema.measure else None
        if measure is None:
            return
        measure = Decimal(measure)
        self.overall.add(measure)
        for agg in self.schema.aggregates:
            if agg.where and any(str(row.get(c)) != v for c, v in agg.where):
                continue
            if agg.exclude and any(str(row.get(c)) in vs for c, vs in agg.exclude):
                continue
            key = row.get(agg.column)
            if key is None and agg.type != "group":
                continue
            buckets = self._buckets[agg.name]
            bucket_key = _bucket_key(agg, key)
            stat = buckets.get(bucket_key)
            if stat is None:
                stat = buckets[bucket_key] = _Stat()
            stat.add(measure)

    def add_rejected(self, reasons: list[str]) -> None:
        self.rejected += 1
        self.reasons.update(reasons)

    def _number(self, value: Decimal | int, func: str) -> float | int:
        if func == "count":
            return int(value)
        scale = self._mean_scale if func == "mean" else self._scale
        rounded = round_to(Decimal(value), scale)
        return int(rounded) if scale == 0 else float(rounded)

    def metrics(self) -> dict[str, float | int]:
        out: dict[str, float | int] = {}
        for name, func in self.schema.metrics.items():
            if func == "count":
                out[name] = self.accepted
            else:
                out[name] = self._number(self.overall.result(func), func)
        return out

    def aggregates(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for agg in self.schema.aggregates:
            buckets = self._buckets[agg.name]
            if agg.type == "daily":
                out[agg.name] = [
                    {"date": day.isoformat(), "total": self._number(stat.total, "sum"),
                     "count": stat.count}
                    for day, stat in sorted(buckets.items())
                ]
            elif agg.type == "yearly":
                out[agg.name] = {str(year): self._number(stat.result(agg.func), agg.func)
                                 for year, stat in sorted(buckets.items())}
            else:
                values = {str(key): self._number(stat.result(agg.func), agg.func)
                          for key, stat in buckets.items()}
                out[agg.name] = dict(sorted(values.items(), key=lambda kv: (-kv[1], kv[0])))
        return out

    def acceptance_rate(self) -> float:
        return round(100 * self.accepted / self.total, 2) if self.total else 0.0

    def build(self, **meta: Any) -> dict[str, Any]:
        """Assemble the report; meta (job_id, source, timings, outputs...) goes first."""
        report = dict(meta)
        report["dataset"] = self.schema.dataset
        report["load_type"] = self.schema.load_type
        report["counts"] = {"total": self.total, "accepted": self.accepted,
                            "rejected": self.rejected}
        report["acceptance_rate"] = self.acceptance_rate()
        report["rejection_reasons"] = dict(self.reasons.most_common())
        report["metrics"] = self.metrics()
        report["metric_specs"] = dict(self.schema.metrics)
        report["aggregates"] = self.aggregates()
        report["aggregate_specs"] = {
            a.name: {"type": a.type, "column": a.column, "func": a.func,
                     "where": dict(a.where),
                     "exclude": {c: len(vs) for c, vs in a.exclude}}
            for a in self.schema.aggregates}
        report["measure"] = self.schema.measure
        report["rules_applied"] = self.schema.rules
        return report
