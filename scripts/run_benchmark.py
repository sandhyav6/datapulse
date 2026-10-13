"""Measure the deployed pipeline and write docs/evidence/benchmark.md.

Part 1 (throughput): generates 1k / 10k / 50k-row files, uploads them one at a time, waits for
each job and reads duration_ms, row counts and acceptance rate from DynamoDB.
Part 2 (burst, scenario 6): uploads N small files at once and records the peak queue depth, the
peak Lambda concurrency (must stay at MaximumConcurrency = 2), throttles and how long the queue
took to drain.

  python scripts/run_benchmark.py                       # 1k, 10k, 50k + burst of 20
  python scripts/run_benchmark.py --sizes 1000 --burst 0

Every number in the output is measured; nothing is estimated. Costs fractions of a cent.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_sample  # noqa: E402
from _stack import add_common_args, job_id_for, session, stack_outputs, wait_for_job  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "evidence" / "benchmark.md"


@dataclass
class JobResult:
    label: str
    rows: int
    size_bytes: int
    status: str
    duration_ms: int | None
    accepted: int | None
    rejected: int | None
    acceptance_rate: float | None
    end_to_end_s: float | None
    job_id: str

    @property
    def rows_per_second(self) -> float | None:
        if not self.duration_ms or self.rows is None:
            return None
        return self.rows / (self.duration_ms / 1000)


@dataclass
class BurstResult:
    files: int
    succeeded: int
    failed: int
    drain_s: float
    peak_queue_depth: int
    peak_concurrency: float | None
    throttles: float | None
    durations_ms: list[int] = field(default_factory=list)


def make_file(rows: int, bad_ratio: float, seed: int) -> Path:
    path = ROOT / "sample_data" / f"bench_{rows}.csv"
    if not path.exists():
        generate_sample.main(["--rows", str(rows), "--bad-ratio", str(bad_ratio),
                              "--seed", str(seed), "--out", str(path)])
        path.with_suffix(".expected.json").unlink(missing_ok=True)
    return path


def upload(s3, bucket: str, path: Path, name: str) -> tuple[str, str, float]:
    key = f"raw/sales/{name}"
    with open(path, "rb") as fh:
        response = s3.put_object(Bucket=bucket, Key=key, Body=fh, ContentType="text/csv")
    return key, job_id_for(bucket, key, response["ETag"]), time.monotonic()


def to_result(label: str, rows: int, size: int, job_id: str, item: dict | None,
              e2e: float | None) -> JobResult:
    item = item or {}
    rate = item.get("acceptance_rate")
    return JobResult(
        label=label, rows=int(item.get("rows_total", rows)), size_bytes=size,
        status=item.get("status", "TIMEOUT"),
        duration_ms=int(item["duration_ms"]) if "duration_ms" in item else None,
        accepted=int(item["rows_accepted"]) if "rows_accepted" in item else None,
        rejected=int(item["rows_rejected"]) if "rows_rejected" in item else None,
        acceptance_rate=float(rate) if rate is not None else None,
        end_to_end_s=e2e, job_id=job_id)


def run_throughput(s3, table, bucket: str, sizes: list[int], bad_ratio: float,
                   run_id: str) -> list[JobResult]:
    results = []
    for rows in sizes:
        path = make_file(rows, bad_ratio, seed=rows)
        key, job_id, started = upload(s3, bucket, path, f"bench_{rows}_{run_id}.csv")
        print(f"uploaded {key} ({path.stat().st_size / 1024:,.0f} KB), waiting for {job_id}...")
        item = wait_for_job(table, job_id, timeout=300, poll=1)
        e2e = time.monotonic() - started if item and item.get("status") else None
        result = to_result(f"{rows:,} rows", rows, path.stat().st_size, job_id, item, e2e)
        print(f"  {result.status}: {result.duration_ms} ms, "
              f"{(result.rows_per_second or 0):,.0f} rows/s")
        results.append(result)
    return results


def run_burst(s3, sqs, cloudwatch, table, outputs: dict, files: int, run_id: str) -> BurstResult:
    path = make_file(1000, 0.05, seed=1000)
    bucket = outputs["BucketName"]
    window_start = datetime.now(UTC) - timedelta(minutes=1)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=files) as pool:
        uploads = list(pool.map(
            lambda i: upload(s3, bucket, path, f"burst_{run_id}_{i:02d}.csv"), range(files)))
    print(f"burst: uploaded {files} files at once, watching the queue...")

    peak_depth, pending = 0, {job_id for _, job_id, _ in uploads}
    items: dict[str, dict] = {}
    deadline = time.monotonic() + 600
    while pending and time.monotonic() < deadline:
        attrs = sqs.get_queue_attributes(
            QueueUrl=outputs["QueueUrl"],
            AttributeNames=["ApproximateNumberOfMessages",
                            "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        depth = int(attrs["ApproximateNumberOfMessages"]) + int(
            attrs["ApproximateNumberOfMessagesNotVisible"])
        peak_depth = max(peak_depth, depth)
        for job_id in list(pending):
            item = table.get_item(Key={"job_id": job_id}).get("Item")
            if item and item.get("status") in ("SUCCEEDED", "FAILED"):
                items[job_id] = item
                pending.discard(job_id)
        time.sleep(2)
    drain_s = time.monotonic() - started

    print("burst: waiting 90 s for CloudWatch Lambda metrics to arrive...")
    time.sleep(90)
    function = outputs["FunctionName"]

    def metric(name: str, stat: str) -> float | None:
        points = cloudwatch.get_metric_statistics(
            Namespace="AWS/Lambda", MetricName=name,
            Dimensions=[{"Name": "FunctionName", "Value": function}],
            StartTime=window_start, EndTime=datetime.now(UTC), Period=60,
            Statistics=[stat])["Datapoints"]
        values = [p[stat] for p in points]
        if not values:
            return None
        return max(values) if stat == "Maximum" else sum(values)

    statuses = [i.get("status") for i in items.values()]
    return BurstResult(
        files=files, succeeded=statuses.count("SUCCEEDED"), failed=statuses.count("FAILED"),
        drain_s=drain_s, peak_queue_depth=peak_depth,
        peak_concurrency=metric("ConcurrentExecutions", "Maximum"),
        throttles=metric("Throttles", "Sum") or 0.0,
        durations_ms=[int(i["duration_ms"]) for i in items.values() if "duration_ms" in i])


def p95(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=20, method="inclusive")[18])


def fmt(value, spec: str = ",.0f", suffix: str = "") -> str:
    return "—" if value is None else f"{value:{spec}}{suffix}"


def render(results: list[JobResult], burst: BurstResult | None, meta: dict) -> str:
    lines = [
        "# Benchmark results",
        "",
        f"Measured {meta['when']} against stack `{meta['stack']}` in `{meta['region']}` "
        f"(Lambda arm64, 512 MB, Python 3.12). Produced by `scripts/run_benchmark.py`; "
        "every figure is read from the jobs table or CloudWatch, nothing is estimated.",
        "",
        "## Throughput (one file at a time)",
        "",
        "| File | Size | Status | Job duration | Rows/s | Accepted | Rejected | Acceptance "
        "| Upload → SUCCEEDED |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(
            f"| {r.label} | {r.size_bytes / 1024:,.0f} KB | {r.status} | "
            f"{fmt(r.duration_ms, ',d', ' ms')} | {fmt(r.rows_per_second)} | "
            f"{fmt(r.accepted, ',d')} | {fmt(r.rejected, ',d')} | "
            f"{fmt(r.acceptance_rate, '.2f', '%')} | {fmt(r.end_to_end_s, '.1f', ' s')} |")
    all_durations = [r.duration_ms for r in results if r.duration_ms is not None]
    if burst:
        all_durations += burst.durations_ms
    lines += [
        "",
        f"p95 job duration across all benchmark jobs: **{fmt(p95(all_durations), ',d', ' ms')}**"
        f" ({len(all_durations)} jobs).",
        "",
        "*Job duration* is measured inside the Lambda (claim → report written). "
        "*Upload → SUCCEEDED* is measured by this script from the end of the upload to seeing the "
        "job finished (1 s polling), so it includes S3 → SQS → Lambda delivery.",
    ]
    if burst:
        lines += [
            "",
            f"## Burst: {burst.files} files uploaded at once (scenario 6)",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Jobs succeeded / failed | {burst.succeeded} / {burst.failed} |",
            f"| Peak queue depth (waiting + in flight) | {burst.peak_queue_depth} |",
            f"| Peak Lambda concurrency | {fmt(burst.peak_concurrency, '.0f')} |",
            f"| Lambda throttles | {fmt(burst.throttles, '.0f')} |",
            f"| Time to drain the queue | {burst.drain_s:.1f} s |",
            f"| p95 job duration in the burst | {fmt(p95(burst.durations_ms), ',d', ' ms')} |",
            "",
            "Expected: queue depth rises, concurrency holds at the `MaximumConcurrency` of 2, "
            "zero throttles, every job SUCCEEDED.",
        ]
    lines += ["", "## Job IDs", ""] + [f"- {r.label}: `{r.job_id}`" for r in results]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    parser.add_argument("--bad-ratio", type=float, default=0.05)
    parser.add_argument("--burst", type=int, default=20, help="files in the burst test (0 = skip)")
    parser.add_argument("--out", type=Path, default=OUT)
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    s3 = sess.client("s3")
    table = sess.resource("dynamodb").Table(outputs["TableName"])
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")

    results = run_throughput(s3, table, outputs["BucketName"], args.sizes, args.bad_ratio, run_id)
    burst = None
    if args.burst:
        burst = run_burst(s3, sess.client("sqs"), sess.client("cloudwatch"), table, outputs,
                          args.burst, run_id)
    meta = {"when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "stack": args.stack,
            "region": args.region}
    args.out.write_text(render(results, burst, meta), encoding="utf-8")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
