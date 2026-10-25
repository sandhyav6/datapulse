"""Compare Athena bytes scanned for the SAME rows stored as CSV and as Parquet (V2).
Writes docs/evidence/athena-comparison.md.

Pick a sales job processed after EnableAnalytics=true (it has both a processed/ CSV and a
curated/ Parquet output):

  python scripts/compare_formats.py --job-id 5e94a237b6d6e354

Why temporary tables: the permanent `sales_csv` table also covers CSV from jobs that ran before
Parquet output was enabled, and Athena does not prune files by "$path", so querying it would
scan more rows than the Parquet side. The script therefore creates two temporary external
tables pointing at exactly this job's CSV folder and Parquet folder, runs the same queries on
both, and drops both tables afterwards. Same rows, exact bytes scanned.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, session, stack_outputs  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "athena-comparison.md"

COLUMNS = ("transaction_id string, transaction_date date, product_category string, "
           "quantity bigint, unit_price decimal(18,2), region string, "
           "transaction_amount decimal(18,2)")

# {table} is replaced by the temporary CSV table or the temporary Parquet table.
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


def folder(uri: str) -> str:
    """s3://b/processed/.../job_id=x/part-0000.csv -> s3://b/processed/.../job_id=x/"""
    return uri.rsplit("/", 1)[0] + "/"


def ddl(table: str, location: str, fmt: str) -> str:
    if fmt == "csv":
        return (f"CREATE EXTERNAL TABLE {table} ({COLUMNS}) ROW FORMAT DELIMITED "
                f"FIELDS TERMINATED BY ',' STORED AS TEXTFILE LOCATION '{location}' "
                "TBLPROPERTIES ('skip.header.line.count'='1')")
    return f"CREATE EXTERNAL TABLE {table} ({COLUMNS}) STORED AS PARQUET LOCATION '{location}'"


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
    stats = execution.get("Statistics", {})
    return {"bytes": stats.get("DataScannedInBytes", 0),
            "ms": stats.get("TotalExecutionTimeInMillis", 0)}


def reduction(csv_bytes: int, parquet_bytes: int) -> float | None:
    return None if not csv_bytes else 100 * (1 - parquet_bytes / csv_bytes)


def render(rows: list[dict], meta: dict) -> str:
    lines = [
        "# CSV vs Parquet: Athena bytes scanned",
        "",
        f"Measured {meta['when']} in workgroup `{meta['workgroup']}`, database "
        f"`{meta['database']}`, on the **same {meta.get('rows', '?')} rows**: the clean output of "
        f"job `{meta.get('job_id', '?')}`, read once from its `processed/` CSV and once from its "
        "`curated/` snappy Parquet (temporary tables over exactly those two folders). "
        "Produced by `scripts/compare_formats.py`.",
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
              "", "Parquet is columnar: a query reads only the columns it uses, and `COUNT(*)` "
              "can be answered from Parquet metadata alone. CSV must be read in full every "
              "time. Athena bills per byte scanned (10 MB minimum per query), so at this tiny "
              "scale both cost fractions of a cent; the ratio is what carries over to real "
              "volumes."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job-id", required=True,
                        help="a sales job processed with EnableAnalytics=true")
    parser.add_argument("--out", type=Path, default=OUT)
    add_common_args(parser)
    args = parser.parse_args()
    if not args.job_id.isalnum():
        sys.exit("job id must be alphanumeric")

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    workgroup, database = outputs.get("AthenaWorkgroupName"), outputs.get("GlueDatabaseName")
    if not (workgroup and database):
        sys.exit("V2 is not deployed: run sam deploy with EnableAnalytics=true first.")
    item = sess.resource("dynamodb").Table(outputs["TableName"]).get_item(
        Key={"job_id": args.job_id}).get("Item") or {}
    job_outputs = item.get("outputs", {})
    if "processed" not in job_outputs or "curated" not in job_outputs:
        sys.exit(f"job {args.job_id} has no CSV + Parquet pair; process a file with "
                 "EnableAnalytics=true and use its job id.")

    athena = sess.client("athena")
    tables = {"csv": f"cmp_csv_{args.job_id}", "parquet": f"cmp_parquet_{args.job_id}"}
    locations = {"csv": folder(job_outputs["processed"]),
                 "parquet": folder(job_outputs["curated"])}
    rows = []
    try:
        for fmt, table in tables.items():
            run_query(athena, workgroup, database, f"DROP TABLE IF EXISTS {table}")
            run_query(athena, workgroup, database, ddl(table, locations[fmt], fmt))
        for name, template in QUERIES.items():
            csv_stats = run_query(athena, workgroup, database,
                                  template.format(table=tables["csv"]))
            pq_stats = run_query(athena, workgroup, database,
                                 template.format(table=tables["parquet"]))
            red = reduction(csv_stats["bytes"], pq_stats["bytes"])
            print(f"{name:<18} csv {csv_stats['bytes']:>10,} B   parquet "
                  f"{pq_stats['bytes']:>10,} B   {'' if red is None else f'{red:.1f}% less'}")
            rows.append({"name": name, "csv": csv_stats, "parquet": pq_stats})
    finally:
        for table in tables.values():
            run_query(athena, workgroup, database, f"DROP TABLE IF EXISTS {table}")

    meta = {"when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "workgroup": workgroup,
            "database": database, "job_id": args.job_id,
            "rows": f"{int(item.get('rows_accepted', 0)):,}"}
    args.out.write_text(render(rows, meta), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
