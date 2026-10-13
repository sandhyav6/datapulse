# ADR 007 — Automatic ingestion of public CSV datasets

**Status:** accepted

## Context
The goal is an application that **automatically ingests public CSV datasets**, validates and
transforms them, stores clean data and processing metadata, and lets users explore analytics
on a dashboard. V1 only processed files someone uploaded. Nothing fetched public data, and
the only data was synthetic.

Public datasets behave differently from uploaded sales files:
- The publisher replaces the whole file when it updates. Each download is a **snapshot**, not
  new rows to append.
- Most days nothing has changed.
- Real files carry real quirks: month-only dates (`1850-01`), 4-decimal measurements,
  composite natural keys (`source + month`, `country + year`), and World Bank aggregate rows
  ("World", "South Asia") mixed in with countries.

## Decision
1. **A separate `dataset-ingester` Lambda**, triggered daily by **EventBridge Scheduler**
   (`cron(30 6 * * ? *)` in `Asia/Kolkata`, a parameter) and on demand via
   `scripts/ingest_now.py` or the dashboard's **Fetch now** button.
2. **Sources are config.** `src/sources.json` lists dataset, HTTPS URL, publisher, license and a
   size cap. Adding a public dataset means adding a source entry plus a schema. No code
   changes.
3. **Change detection by content hash.** The ingester downloads the CSV (HTTPS only, ≤ 5 MB,
   refused rather than truncated if larger), computes its sha256 and compares it with the
   `sha256` metadata on the current `raw/<dataset>/<dataset>.csv`. If they match it writes
   nothing, so no new object version, S3 event or pipeline job is created. If the content
   changed, it writes the file with `sha256`, `source-url` and `fetched-at` metadata.
4. **The ingester only lands data; the pipeline does everything else.** Its write is an
   ordinary `raw/` upload, so the existing S3 → SQS → etl-processor path, with idempotency,
   DLQ, alarms and reports, applies unchanged. Its IAM role can only `GetObject`/`PutObject`
   under `raw/`.
5. **Snapshot-aware schemas and analytics.** Schemas gain `load_type`
   (`incremental` | `snapshot`), composite `dedup_key`, decimal `scale`, aggregate functions
   (`sum`, `mean`, `min`, `max`, `count`), `yearly` aggregates, and `where` / `exclude` filters.
   The dashboard shows the **latest** report for snapshot datasets and **combines** reports for
   incremental ones. Incremental datasets may only use additive functions (`sum`, `count`),
   enforced at schema load, because averages cannot be summed across files.
6. **Failures are visible.** One failing source does not stop the others. A run with any failure
   raises, so Lambda `Errors` increments and the new `IngesterErrorsAlarm` notifies via SNS.
   Scheduler retries twice, which is safe because unchanged content is skipped.

## Datasets shipped
| Dataset | Source | License | Size | Schema highlights |
|---|---|---|---|---|
| `global_temperature` | datasets/global-temp `monthly.csv` | CC-BY-4.0 | 81 KB, ~3.9k rows | `%Y-%m` dates, 4-dp decimals, key `source + month`, yearly mean per record |
| `world_population` | datasets/population `population.csv` (World Bank SP.POP.TOTL) | ODC-PDDL-1.0 | 557 KB, ~17.4k rows | key `country_code + year`, World/India/China series via `where`, country ranking via `exclude` of 49 World Bank aggregate codes |

Both were run through the real pipeline against the live files before being adopted, and both
were 100% accepted. A third candidate, the Mauna Loa CO2 file (datasets/co2-ppm), was
**rejected**: its header names 6 columns but every row has 7 values, so every row would
correctly fail as `malformed_row`. The schema-first design caught a real upstream defect before
it could produce misleading charts.

## Alternatives considered
- **EventBridge rule (`rate(1 day)`) instead of Scheduler.** Works too, but Scheduler supports
  time zones, has a retry policy per schedule, and has 14M free invocations a month. SAM's
  `ScheduleV2` creates a role that can invoke only this function.
- **Fetching inside etl-processor.** It would mix two failure domains (publisher outages vs.
  data errors) and require the processor to have internet-facing behaviour and a schedule.
  Separate functions keep each role minimal.
- **Writing a new key per day** (`raw/<ds>/<date>.csv`). Every day would create a job even when
  nothing changed. A stable key plus hash comparison means a job only when the data changed,
  and S3 versioning still keeps every past snapshot.
- **ETag or `Last-Modified` from the publisher.** Not every host provides them reliably. A
  content hash is unambiguous and costs one small download.
- **Glue crawlers or AppFlow.** Paid services for schema discovery and connectors we don't need.
  The schemas are known and the sources are plain HTTPS.

## Consequences
- An unchanged day costs one GET per source to the publisher and one HEAD to S3. No new
  objects or jobs, and no data-transfer charge for inbound internet traffic.
- Custom metrics no longer carry a `Dataset` dimension (see ADR 002), so adding datasets never
  pushes CloudWatch past the 10 free custom metrics.
- The ingester trusts `sources.json`, which is code-reviewed config. It still enforces HTTPS and
  a size cap, and the pipeline validates every row whatever the source.
