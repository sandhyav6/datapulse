# Benchmark results

Measured 2026-10-25 03:56 UTC against stack `serverless-etl-dev` in `ap-south-1` (Lambda arm64, 512 MB, Python 3.12). Produced by `scripts/run_benchmark.py`; every figure is read from the jobs table or CloudWatch, nothing is estimated.

## Throughput (one file at a time)

| File | Size | Status | Job duration | Rows/s | Accepted | Rejected | Acceptance | Upload → SUCCEEDED |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1,000 rows | 47 KB | SUCCEEDED | 189 ms | 5,291 | 950 | 50 | 95.00% | 1.1 s |
| 10,000 rows | 466 KB | SUCCEEDED | 823 ms | 12,151 | 9,500 | 500 | 95.00% | 2.1 s |
| 50,000 rows | 2,327 KB | SUCCEEDED | 3,816 ms | 13,103 | 47,500 | 2,500 | 95.00% | 5.2 s |

p95 job duration across all benchmark jobs: **770 ms** (23 jobs).

*Job duration* is measured inside the Lambda (claim → report written). *Upload → SUCCEEDED* is measured by this script from the end of the upload to seeing the job finished (1 s polling), so it includes S3 → SQS → Lambda delivery.

## Burst: 20 files uploaded at once (scenario 6)

| Metric | Value |
|---|---:|
| Jobs succeeded / failed | 20 / 0 |
| Peak queue depth (waiting + in flight) | 14 |
| Peak Lambda concurrency | 2 |
| Lambda throttles | 0 |
| Time to drain the queue | 29.6 s |
| p95 job duration in the burst | 216 ms |

Expected: queue depth rises, concurrency holds at the `MaximumConcurrency` of 2, zero throttles, every job SUCCEEDED.

## Job IDs

- 1,000 rows: `53353698d4080578`
- 10,000 rows: `a5823c7ece0ae47b`
- 50,000 rows: `7de9a9301194a6f0`
