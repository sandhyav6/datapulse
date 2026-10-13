# ADR 005 — Lambda, not Glue ETL jobs, at this data size

**Status:** accepted

## Context
Input files are CSVs from kilobytes to a few megabytes (the benchmark's largest file is
50,000 rows ≈ 2.4 MB). Each file is processed independently: validate, clean, de-duplicate
within the file, write outputs and a report. There are no cross-file joins.

## Decision
Process each file in a Python 3.12 Lambda (arm64, 512 MB, 60 s timeout) that streams the
object with `GetObject` and `csv.reader`, and keeps only counters and small aggregates in
memory.

## Comparison
| | Lambda (chosen) | Glue ETL job (Spark or Python shell) |
|---|---|---|
| Start-up | milliseconds (warm) to ~1 s (cold) | tens of seconds to minutes per run |
| Minimum billing | 1 ms granularity | 1-minute minimum; Spark jobs bill ≥ 2 DPUs |
| Free plan | 1M requests + 400,000 GB-s/month always free | not free; per DPU-hour |
| Trigger | SQS event source mapping | needs a trigger (EventBridge/workflow) |
| Fits when | file fits in time/memory budget, per-file logic | large data, joins, Spark transforms |
| Operations | one function, one log group | job, script location, temp dirs, bookmarks |

## When this flips to Glue
- A single file needs more than Lambda's 15-minute timeout or 10 GB memory.
- Processing needs joins or aggregations across many files or large reference tables.
- Volume makes per-file invocations less efficient than distributed batch processing.

The first sign would come from `JobDurationMs` p95 approaching the 60 s timeout: raise memory
first (Lambda CPU scales with it), then reconsider.

## Performance work that keeps Lambda comfortable
Profiling the row pipeline on a 50,000-row file found that `datetime.strptime` took ~40% of the
time (locale lookups on every call) and that each value was parsed twice (validate, then
transform). Two changes cut pure processing time 3.3× (7.56 s → 2.29 s on the development
laptop), with identical outputs and all tests green:
1. `parse_row` validates and type-converts in one pass, and the transformer reuses the parsed
   values.
2. Strict regex fast paths for common date formats, with `strptime` kept as the fallback.
   Derived-field formulas are compiled to closures once at schema load instead of walking the
   AST per row.

Measured Lambda figures are in `docs/evidence/benchmark.md`.

## Consequences
- No pandas in the function: the package stays a few KB and `sam build` needs no Docker.
  (V2's Parquet writer imports pyarrow from the AWS-managed layer only when enabled.)
- Memory is bounded: rows stream through. Only the dedup key set and the aggregates grow, with
  distinct IDs and distinct dates/categories respectively.
