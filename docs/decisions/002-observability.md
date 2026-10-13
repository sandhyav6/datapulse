# ADR 002 — Observability: EMF metrics, three alarms, one dashboard

**Status:** accepted

## Context
We need to know, without opening logs, (a) whether the pipeline is healthy and (b) whether the
*data* is healthy, while staying inside CloudWatch's always-free allowance (10 custom metrics,
10 alarms, 3 dashboards, 5 GB log ingestion).

## Decision
- **Structured logs.** Every log line is one JSON object (`print(json.dumps(...))`) carrying
  `job_id` and an `event` name (`job_claimed`, `stage`, `job_finished`, `job_failed`,
  `duplicate_delivery`, ...). Logs Insights discovers the fields automatically.
- **Custom metrics through Embedded Metric Format.** One log line per finished job declares
  `RowsAccepted`, `RowsRejected`, `JobDurationMs` and `JobsFailed` in namespace `EtlPipeline`
  with the single dimension `Dataset`. No `PutMetricData` calls, no extra IAM permission, no
  added latency. Unknown datasets are reported as `Dataset=unknown`, so a stray upload cannot
  create new metric series. Two datasets = 8 custom metrics (under the 10 free).
- **Three alarms**, all `TreatMissingData: notBreaching`, all notifying the `AlertsTopic` SNS
  topic:
  | Alarm | Signal | Why |
  |---|---|---|
  | `LambdaErrorsAlarm` | `AWS/Lambda Errors` Sum ≥ 1 / 5 min | crashes the handler could not catch: timeouts, OOM, import errors |
  | `DlqDepthAlarm` | DLQ `ApproximateNumberOfMessagesVisible` ≥ 1 | a job exhausted its retries (poison file, persistent bug) |
  | `QueueAgeAlarm` | `ApproximateAgeOfOldestMessage` > 600 s | the pipeline is stuck or throttled |
- **One CloudWatch dashboard** (`etl-pipeline-<stage>`) with Lambda invocations/errors/throttles,
  duration p95, concurrency, queue depth, DLQ depth, oldest-message age, the four custom metrics
  (via `SEARCH` so new datasets appear automatically) and a Logs Insights table of recent jobs.
- **Saved Logs Insights queries**: `recent-jobs` (last 50 jobs) and `job-trace` (every line of
  one job).
- **14-day log retention** instead of the default "never expire".

## Job failures vs Lambda errors
The handler uses `ReportBatchItemFailures`: a failing record is returned in
`batchItemFailures` rather than raising, so with larger batches one bad file never forces a
whole batch to retry. The consequence is that a *handled* job failure does not increment
`AWS/Lambda Errors`. That is deliberate: job failures are tracked by `JobsFailed` (emitted
once, on the final delivery) and, decisively, by `DlqDepthAlarm`; `LambdaErrorsAlarm` is kept
for genuine crashes.

## Alternatives considered
- `PutMetricData` from the function: extra API call per job, extra IAM permission, cost.
- Metric filters on logs: work, but each filter is configured separately and cannot carry
  several values from one line.
- X-Ray tracing: useful for multi-service request paths; this path is one function, and the
  `job_id` in every log line already gives an end-to-end trace.
