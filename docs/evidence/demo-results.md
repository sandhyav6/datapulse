# Demo results on the deployed stack

Stack `serverless-etl-dev`, account 129188092622, region `ap-south-1`, run 2026-10-24 and
2026-10-25 (UTC). Every value below was read back from DynamoDB, S3, SQS, CloudWatch or Athena
after the action. Nothing is estimated.

## Scenario 0: automatic ingestion of public datasets

`python scripts/ingest_now.py` invokes the `dataset-ingester-dev` Lambda.

| Run | global_temperature | world_population |
|---|---|---|
| 1st | `uploaded`, 81,109 bytes | `uploaded`, 556,718 bytes |
| 2nd (same day) | `unchanged`, sha256 matched, nothing written | `unchanged` |

Resulting pipeline jobs:

| Dataset | Rows | Accepted | Acceptance | Job duration | Attempts |
|---|---:|---:|---:|---:|---:|
| global_temperature | 3,871 | 3,871 | 100% | 311 ms | 1 |
| world_population | 17,394 | 17,394 | 100% | 1,013 ms | 1 |

## Scenario 1: happy path (`clean_small.csv`)

Job `5ef6f72742365489`: SUCCEEDED, 100 / 100 accepted, 121 ms, total_sales 79,090.12.

## Scenario 2: data quality (`dirty_small.csv`)

Job `eb5bf1873e7c3d7a`: SUCCEEDED, 1,000 rows, 950 accepted, 50 rejected (95.00%), 272 ms.
The rejection reasons match `sample_data/dirty_small.expected.json` exactly:

| Reason | Rows |
|---|---:|
| duplicate_id | 10 |
| invalid_transaction_date | 10 |
| invalid_unit_price | 10 |
| missing_transaction_id | 10 |
| negative_quantity | 10 |

total_sales 726,980.02, identical to the generator's expected total.

## Scenario 3: duplicate delivery

`python scripts/replay_event.py raw/sales/dirty_small.csv --times 3` sent 3 copies of the S3
event to the queue.

| | Status | Attempts | Rows accepted | Output objects |
|---|---|---:|---:|---:|
| Before | SUCCEEDED | 1 | 950 | 12 |
| After 3 replays | SUCCEEDED | 1 | 950 | 12 |

Script verdict: `IDEMPOTENT: nothing changed`.

## Scenario 4: poison file (`poison.csv`, missing `unit_price`)

Job `603022fde2c40966`: FAILED on every delivery with
`HeaderValidationError: missing required columns: unit_price`. After the 3rd receive the
message was in `etl-jobs-dlq-dev` (uploaded 03:53:36, DLQ depth 2 at 04:11:49 UTC, together
with scenario 5's message).

## Scenario 5: recovery by code fix and redrive

1. `returns_small.csv` was uploaded to `raw/returns/` before any `returns` schema existed. Job
   `d30d98363368c683` was FAILED after 3 attempts (`UnknownDatasetError: no schema for dataset
   'returns'`) and its message went to the DLQ.
2. Fix: `src/schemas/returns.json` was added and the stack redeployed.
3. `bash scripts/redrive_dlq.sh` moved both DLQ messages back with an SQS message move task.

| Job | Before redrive | After redrive |
|---|---|---|
| returns `d30d98363368c683` | FAILED, attempts 3 | **SUCCEEDED, attempts 4**, 50 rows accepted |
| poison `603022fde2c40966` | FAILED, attempts 3 | FAILED, attempts 4 (the file itself is broken, as expected) |

## Scenario 6: burst load

See [benchmark.md](benchmark.md): 20 files at once, all 20 SUCCEEDED, peak concurrency **2**
(= `MaximumConcurrency`), **0 throttles**, queue drained in 29.6 s.

## V2: Parquet + Glue + Athena

- Deployed with `EnableAnalytics=true`. The Lambda runs with `OUTPUT_FORMAT=both` and the
  `AWSSDKPandas-Python312-Arm64:31` layer.
- Job `5e94a237b6d6e354` (`dirty_v2.csv`) wrote both
  `processed/…/part-0000.csv` and `curated/…/part-0000.parquet` (21,689 bytes).
- The saved query *monthly-revenue* on the Parquet `sales` table returned 2026-09:
  543,194.63 (706 rows) and 2026-10: 183,785.39 (244 rows). Together that is 950 rows and
  726,980.02, the same total the pipeline reported.
- CSV vs Parquet bytes scanned on identical rows: see
  [athena-comparison.md](athena-comparison.md) (85.8–87.6% less for analytic queries).
