# ADR 006 — Parquet, a Glue catalog with partition projection, and Athena (V2)

**Status:** accepted (V2, enabled with `EnableAnalytics=true`)

## Context
V1 answers "how did this file do?" through per-job reports. V2 adds questions across all files
(monthly revenue, top categories, region by month) without running a database or a cluster,
while staying close to free.

## Decision
1. **Parquet output in a separate `curated/` zone.** With `EnableAnalytics=true` the Lambda
   writes each job's clean rows as snappy Parquet to
   `curated/dataset=sales/ingest_date=<d>/job_id=<id>/part-0000.parquet`, alongside the CSV in
   `processed/` (`OUTPUT_FORMAT=both`). The key is still derived from `job_id`, so retries
   overwrite.
   - *Why a separate prefix rather than Parquet next to the CSV in `processed/`:* Athena reads
     every file under a table's location, so mixing formats in one prefix breaks both tables,
     and keeping the CSV lets us measure CSV against Parquet on identical rows.
   - Types are explicit: `date`, `bigint`, `decimal(18,2)`, so money is never a float.
2. **pyarrow from the AWS-managed "AWS SDK for pandas" layer**, attached only when analytics
   is on, and imported lazily. The V1 function package stays stdlib + boto3.
   ARN for python3.12 / arm64 / ap-south-1:
   `arn:aws:lambda:ap-south-1:336392948345:layer:AWSSDKPandas-Python312-Arm64:31`, from the
   official layer list at https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html (v3.17.1).
   It is a template parameter (`PandasLayerArn`) so a newer version is a deploy-time change.
3. **Glue tables defined in the template, not discovered by a crawler.** `sales` (Parquet,
   `curated/`) and `sales_csv` (CSV, `processed/`) use **partition projection** on
   `ingest_date` (type date, `2026-01-01,NOW`, daily). Athena computes partition locations
   from the query, so no crawler runs (billed per DPU-hour), no `MSCK REPAIR TABLE` is needed,
   and new days are queryable the moment a file lands.
4. **A dedicated Athena workgroup** `etl-pipeline-<stage>` with
   `EnforceWorkGroupConfiguration: true`, results in `athena-results/` (7-day lifecycle,
   SSE-S3) and `BytesScannedCutoffPerQuery: 100 MB`, a hard cost ceiling per query.
5. **Dashboard access through IAM, not trust.** The dashboard identity may use only this
   workgroup, read only these two Glue tables, read `curated/` and `processed/`, and read/write
   only `athena-results/`. The SELECT-only check in the UI is a convenience; IAM is the control.
   (`s3:GetBucketLocation` sits in its own statement: under the `s3:prefix` condition used for
   `ListBucket` it would always be denied. A test in `tests/unit/test_template.py` guards this.)

## CSV vs Parquet: measured
**Locally**, same 47,500 clean rows (from the 50,000-row benchmark file), written by the
pipeline's own `JobOutputs`:

| | Bytes | vs CSV |
|---|---:|---:|
| CSV (`processed/`) | 2,574,747 | 100% |
| Parquet, snappy (`curated/`) | 876,620 | 34.0% (**66% smaller**) |
| Parquet column chunks a 2-column query needs (`product_category`, `transaction_amount`) | 280,994 | 10.9% (**89% less to read**) |

Parquet is columnar, so a query reads only the columns it references. CSV must be read whole.

**On Athena**, `scripts/compare_formats.py` runs the same five queries on `sales_csv` and
`sales` and records `DataScannedInBytes` for each into
[`docs/evidence/athena-comparison.md`](../evidence/athena-comparison.md). Use those figures,
not the local ones, for the bytes-scanned claim.

## Alternatives considered
- **Glue crawler.** Infers schemas we already know, costs DPU-hours, and must re-run to discover
  partitions.
- **Glue ETL job converting CSV → Parquet.** A second pipeline and a Spark bill for data the
  Lambda already holds in memory (ADR 005).
- **Firehose with format conversion.** Built for record streams, not whole-file batch jobs.
- **Parquet only (drop CSV).** Possible with `OUTPUT_FORMAT=parquet`. CSV is kept by default
  because it is human-readable in the console and the dashboard previews it.

## Consequences
- With `both`, storage roughly doubles for clean rows (still KB–MB). Once the comparison is
  recorded, change `OUTPUT_FORMAT` in `template.yaml` from `both` to `parquet`.
- The layer adds cold-start time when analytics is on. The function package itself is
  unchanged.
- Athena is not in the always-free tier. It costs $5 per TB scanned with a 10 MB minimum per
  query, so each query here costs about $0.00005, capped by the workgroup.
