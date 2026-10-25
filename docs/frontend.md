# Frontend: the React dashboard

**Live:** https://main.d18xoo8gkaam5b.amplifyapp.com (AWS Amplify Hosting, `ap-south-1`)

A React + Vite + TypeScript + Tailwind CSS single-page app. It reads everything from the
read-only JSON API at `/api/*` on the same domain (Amplify reverse-proxies it to API Gateway →
Lambda). See [ADR 008](decisions/008-react-frontend-on-amplify.md) for why it's built this way.

## Pages

| Page | URL | Shows | API |
|---|---|---|---|
| Overview | `/` | files processed, failed jobs, rows, acceptance rate, avg/p95 job time, jobs and rows per day, latest jobs | `/api/overview` |
| Analytics | `/analytics?dataset=…` | every aggregate in the dataset's schema: world/India/China population and largest countries, temperature anomaly by year, sales by day/category/region. Snapshot datasets show the latest version; incremental ones combine all files | `/api/datasets`, `/api/analytics/{dataset}` |
| Sources | `/sources` | each public dataset (publisher, license, link, landing key), the schedule in plain words, and the latest ingestion | `/api/sources` |
| Jobs | `/jobs` | sortable, filterable job list (status, dataset, search) | `/api/jobs` |
| Job detail | `/jobs/{job_id}` | counts, attempts, error, rejection reasons, metrics, output S3 URIs, first 50 rejected rows, full report JSON | `/api/jobs/{id}`, `/api/jobs/{id}/rejected` |
| Data quality | `/quality` | rejections by reason, acceptance trend per job, jobs with rejections | `/api/quality` |
| Operations | `/operations` | queue / in-flight / DLQ depth (refreshes every 15 s), DLQ alert, failed and retrying jobs | `/api/operations` |
| Athena SQL | `/athena` | three saved queries over the Parquet table, with bytes scanned and run time | `/api/athena`, `/api/athena/{name}` |
| How it works | `/about` | the path of one file through the AWS services, and the design guarantees | none |

## Code layout

```
frontend/
  index.html             page shell
  vite.config.ts         Tailwind plugin, dev proxy for /api, Vitest config
  src/main.tsx           React root: TanStack Query + React Router
  src/App.tsx            routes (each page is a lazy-loaded chunk)
  src/lib/api.ts         typed client for every API endpoint
  src/lib/types.ts       response shapes (mirror backend/src/api)
  src/lib/format.ts      numbers, dates (viewer's time zone), names, schedules
  src/components/        Layout (sidebar/mobile menu), ui (cards, KPIs, badges...), charts, JobsTable
  src/pages/             one file per page above
  src/test/              Vitest setup, fetch mock, fixtures
```

Performance: each page and the chart library are separate chunks. The app shell is about
89 KB gzipped, and charts (114 KB) load on first use. Data is cached for 30 s client-side.

## Run it locally

Two terminals. First, the fake AWS plus the **real** API Lambda code, on port 8000:

```bash
cd backend
```
```bash
..\.venv\Scripts\python scripts\local_demo.py
```

Second, the React dev server on port 5173 (it proxies `/api` to port 8000):

```bash
cd frontend
```
```bash
npm run dev
```

Open http://localhost:5173. To develop against the **real** deployed data instead, create
`frontend/.env.local` containing
`VITE_API_PROXY=https://main.d18xoo8gkaam5b.amplifyapp.com` and restart `npm run dev`.

## Test, lint, build

```bash
npm test
```
```bash
npm run lint
```
```bash
npm run typecheck
```
```bash
npm run build
```

The tests (Vitest + Testing Library) render the real app at a URL with the API mocked by fixtures
shaped like the backend's responses. They cover KPIs, filtering and search, opening a job,
snapshot analytics, the DLQ warning, Athena, error states and the 404 page. CI runs all four
commands on every push.

## Deploy

After the stack exists (`sam deploy` in `backend/`):

```bash
cd backend
```
```bash
..\.venv\Scripts\python scripts\deploy_frontend.py
```

This builds `frontend/dist`, zips it, uploads it as an Amplify manual deployment, waits for
`SUCCEED`, and prints the live URL. It takes about 30 seconds.
