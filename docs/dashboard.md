# Operator dashboard (Streamlit)

A local Streamlit app for exploring the public datasets and sales data the pipeline processes,
along with job history, data quality and queue health. It runs as its own IAM identity,
`etl-dashboard`, which can **read** job state and outputs, **upload** to `raw/` and **start**
the public-dataset ingester. It can never modify outputs, job items or queues.

| Page | What it shows | Reads |
|---|---|---|
| Overview | files processed, failed jobs, acceptance rate, avg/p95 duration, jobs and rows per day | DynamoDB `status-index` |
| Jobs | sortable/filterable job table; select a row for its report, outputs, metrics, rejected-row sample and a CloudWatch Logs link | DynamoDB, `reports/`, `rejected/` |
| Data quality | rejections by reason, per job and over time; acceptance-rate trend; rejected rows | DynamoDB, `rejected/` |
| Analytics | every aggregate the dataset's schema defines: yearly lines (world/India/China population, temperature anomaly by record), daily sales, top-15 rankings. Snapshot datasets show their latest version; incremental datasets (sales) are combined across files | `reports/` |
| Sources | each public source (publisher, license, URL, landing key) with its latest job, plus **Fetch now** to run the ingester immediately | `src/sources.json`, DynamoDB; invokes the ingester |
| Operations | queue / in-flight / DLQ depth, failed and retrying jobs with errors, CSV upload to `raw/<dataset>/` | SQS attributes, DynamoDB; writes `raw/` |
| Athena SQL (V2) | saved queries and a SELECT-only SQL box over the Parquet table | Athena, Glue, `curated/`, `athena-results/` |

Jobs are read with `Query` on the `status-index` GSI, one query per status, never a `Scan`.
Results are cached for 30 s (reports for 5 min); **Refresh data** in the sidebar clears the cache.

## Preview it without AWS

```bash
python scripts/local_demo.py
```

This starts a local moto server that plays S3, SQS and DynamoDB. It runs the **real ingester**
against the live public sources (bundled fixtures if offline), then runs the real Lambda code
over those and the sales sample files (clean, dirty, a poison file that fails three times, and
the `returns` dataset), and opens the dashboard at http://localhost:8501. Uploads in this mode are stored but
not processed, because there is no Lambda. Requires `pip install "moto[server]"`.

## Run it against the deployed stack

### 1. Create the dashboard identity (once, after the first deploy)

In the AWS console, signed in with your admin/deploy identity:

1. **IAM → Users → Create user**, name `etl-dashboard`. Leave **console access off**.
2. **Permissions → Attach policies directly**, search `etl-dashboard-dev`, tick it, create the
   user. (This is the `DashboardReadPolicy` the stack created; its ARN is the stack output
   `DashboardPolicyArn`.)
3. Open the user → **Security credentials → Create access key → Local code**. Copy the key
   pair once.
4. Store it as a local profile (the keys go into `~/.aws/credentials`, never into the repo):

   ```bash
   aws configure --profile etl-dashboard
   ```
   Use region `ap-south-1` and output `json`.

Or the same with the CLI, run as your deploy profile:

```bash
aws iam create-user --user-name etl-dashboard --profile etl-dev
```
```bash
aws iam attach-user-policy --user-name etl-dashboard --policy-arn arn:aws:iam::<account-id>:policy/etl-dashboard-dev --profile etl-dev
```
```bash
aws iam create-access-key --user-name etl-dashboard --profile etl-dev
```

### 2. Configure and start

```bash
pip install -r dashboard/requirements.txt
```
```bash
python scripts/make_dashboard_env.py
```
```bash
streamlit run dashboard/app.py
```

`make_dashboard_env.py` reads the stack outputs with your `etl-dev` profile and writes
`dashboard/.env` (gitignored) with `AWS_PROFILE=etl-dashboard` and `INGESTER_FUNCTION` (which
enables **Fetch now**), so the app itself always runs as the restricted identity. To fill it in by hand, copy `dashboard/.env.example`.

### 3. Prove the boundary (optional evidence)

These must fail with `AccessDenied` when run as the dashboard identity:

```bash
aws s3 cp README.md s3://etl-pipeline-<account-id>-ap-south-1/processed/hack.csv --profile etl-dashboard
```
```bash
aws dynamodb scan --table-name etl-jobs-dev --profile etl-dashboard
```

And this must succeed (it starts a normal pipeline job):

```bash
aws s3 cp sample_data/clean_small.csv s3://etl-pipeline-<account-id>-ap-south-1/raw/sales/clean_small.csv --profile etl-dashboard
```

## Design notes
- **Folder name.** Pages live in `dashboard/views/`, not `dashboard/pages/`. Streamlit
  auto-generates its legacy navigation for a folder named `pages/`, which conflicts with the
  explicit `st.navigation` menu used here.
- **Plain functions plus thin caches.** `data.py` keeps AWS access in plain functions that take
  boto3 clients (unit-tested with moto in `tests/dashboard/`). The `load_*` wrappers add
  Streamlit caching. `tests/dashboard/test_pages.py` renders every page with Streamlit's
  `AppTest` against jobs produced by the real pipeline.
- **The SQL guard is for usability only.** The Athena page only accepts a single
  SELECT/WITH statement, but the real control is IAM: the dashboard identity has no Glue write
  permissions and can write to S3 only under `raw/` and `athena-results/`.
