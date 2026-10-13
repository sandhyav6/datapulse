"""Run the same Athena queries on the CSV table and the Parquet table and compare the bytes
scanned (V2). Writes docs/evidence/athena-comparison.md.

Needs the stack deployed with EnableAnalytics=true and at least one sales file processed after
that (so both processed/ CSV and curated/ Parquet exist for the same rows).

  python scripts/compare_formats.py
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, session, stack_outputs  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "athena-comparison.md"

# {table} is replaced by sales_csv (CSV, processed/) or sales (Parquet, curated/).
QUERIES = {
    "Row count": "SELECT COUNT(*) AS n FROM {table}",
    "Monthly revenue": (
        "SELECT date_format(transaction_date, '%Y-%m') AS month, "
        "SUM(transaction_amount) AS revenue FROM {table} GROUP BY 1 ORDER BY 1"),
    "Top 10 categories": (
        "SELECT product_category, SUM(transaction_amount) AS revenue FROM {table} "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
    "Region by month": (
        "SELECT region, date_format(transaction_date, '%Y-%m') AS month, "
        "SUM(transaction_amount) AS revenue FROM {table} GROUP BY 1, 2"),
    "Full row scan": "SELECT * FROM {table} ORDER BY transaction_id LIMIT 5",
}


def run_query(athena, workgroup: str, database: str, sql: str) -> dict:
    query_id = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup,
        QueryExecutionContext={"Database": database})["QueryExecutionId"]
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        raise RuntimeError(f"{sql!r}: {execution['Status'].get('StateChangeReason', state)}")
    stats = execution["Statistics"]
    return {"bytes": stats["DataScannedInBytes"], "ms": stats["TotalExecutionTimeInMillis"]}


def reduction(csv_bytes: int, parquet_bytes: int) -> float | None:
    return None if not csv_bytes else 100 * (1 - parquet_bytes / csv_bytes)


def render(rows: list[dict], meta: dict) -> str:
    lines = [
        "# CSV vs Parquet: Athena bytes scanned",
        "",
        f"Measured {meta['when']} in workgroup `{meta['workgroup']}`, database "
        f"`{meta['database']}`. Same rows in both tables: `sales_csv` reads `processed/` (CSV), "
        "`sales` reads `curated/` (snappy Parquet). Produced by `scripts/compare_formats.py`.",
        "",
        "| Query | CSV scanned | Parquet scanned | Reduction | CSV time | Parquet time |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        red = reduction(r["csv"]["bytes"], r["parquet"]["bytes"])
        lines.append(
            f"| {r['name']} | {r['csv']['bytes']:,} B | {r['parquet']['bytes']:,} B | "
            f"{'—' if red is None else f'{red:.1f}%'} | {r['csv']['ms']:,} ms | "
            f"{r['parquet']['ms']:,} ms |")
    total_csv = sum(r["csv"]["bytes"] for r in rows)
    total_pq = sum(r["parquet"]["bytes"] for r in rows)
    total_red = reduction(total_csv, total_pq)
    lines += ["", f"**Total:** {total_csv:,} B (CSV) vs {total_pq:,} B (Parquet)"
              + ("" if total_red is None else f", **{total_red:.1f}% less data scanned**") + ".",
              "", "Athena bills per byte scanned (10 MB minimum per query), so at this tiny scale "
              "both cost fractions of a cent; the ratio is what carries over to real volumes."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT)
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    workgroup, database = outputs.get("AthenaWorkgroupName"), outputs.get("GlueDatabaseName")
    if not (workgroup and database):
        sys.exit("V2 is not deployed: run sam deploy with EnableAnalytics=true first.")
    athena = sess.client("athena")

    rows = []
    for name, template in QUERIES.items():
        csv_stats = run_query(athena, workgroup, database, template.format(table="sales_csv"))
        pq_stats = run_query(athena, workgroup, database, template.format(table="sales"))
        red = reduction(csv_stats["bytes"], pq_stats["bytes"])
        print(f"{name:<18} csv {csv_stats['bytes']:>12,} B   parquet {pq_stats['bytes']:>12,} B"
              f"   {'' if red is None else f'{red:.1f}% less'}")
        rows.append({"name": name, "csv": csv_stats, "parquet": pq_stats})

    meta = {"when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "workgroup": workgroup,
            "database": database}
    args.out.write_text(render(rows, meta), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
