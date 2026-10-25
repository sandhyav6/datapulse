# ADR 008 — React frontend on Amplify Hosting with a read-only Lambda API

**Status:** accepted (replaces the local Streamlit dashboard)

## Context
The first dashboard was a Streamlit app running on the operator's laptop with its own IAM user
(`etl-dashboard`). It worked, but it could not be shared, it needed long-lived access keys on a
laptop, and it was not a web frontend in the usual sense. The goal is a real web app that
anyone can open: **React + Vite + TypeScript + Tailwind CSS**, hosted on AWS.

A browser cannot safely hold AWS credentials, so the React app needs a backend that reads
DynamoDB, S3, SQS and Athena on its behalf.

## Decision
1. **Monorepo split.** `frontend/` (React + Vite + TypeScript + Tailwind v4, React Router,
   TanStack Query, Recharts) and `backend/` (SAM template, the three Lambdas, tests, scripts).
2. **A read-only JSON API** (`backend/src/api`): one Lambda behind an **API Gateway HTTP API**,
   route `GET /api/{proxy+}`.
   - Its IAM role can only `Query` the status index, `GetItem` a job, read `reports/` and
     `rejected/`, read queue depths, and run Athena in its own workgroup. It cannot write,
     upload, delete, scan or invoke anything (`tests/unit/test_template.py` enforces this).
   - POST/PUT/DELETE get 405 from the Lambda; API Gateway has no route for them at all.
   - Athena: only three **fixed, saved** queries can run from the internet, never
     user-supplied SQL. Results are cached for 15 minutes.
   - Abuse protection for the free 5 RCU: API Gateway throttling (10 req/s, burst 20), short
     in-memory caches in the Lambda (jobs 20 s), and `Cache-Control` headers so browsers reuse
     responses.
   - Errors never leak details: a 500 returns `{"error": "internal error"}` and the stack trace
     goes to CloudWatch Logs only.
3. **Hosting: AWS Amplify Hosting** (`AWS::Amplify::App` + `Branch` in the template).
   - HTTPS on Amplify's global CDN, with security headers (HSTS, nosniff, DENY framing).
   - Hashed `assets/` are cached for a year and `index.html` never, so each deploy shows
     immediately.
   - **Same-origin API:** a reverse-proxy rule sends `/api/<*>` to API Gateway, so the browser
     never makes a cross-origin call and no CORS configuration exists to get wrong.
   - A second rule serves `index.html` for client-side routes (`/jobs/abc`, `/analytics`).
   - Deploys are manual uploads of `frontend/dist`: `backend/scripts/deploy_frontend.py`
     (create deployment → upload zip → start → wait).
4. **The `etl-dashboard` IAM user and its policy were deleted.** The public API replaces them,
   and no human-held access key is needed to view data any more.

## What was tried first: S3 + CloudFront
The original design was a private S3 bucket behind **CloudFront**, using Origin Access Control,
a CloudFront Function for client-side routes, and a second `/api/*` behaviour. The template was
written and validated, but the deploy failed with:

> Your account must be verified before you can add new CloudFront resources. To verify your
> account, please contact AWS Support.

New AWS accounts can be blocked from creating CloudFront distributions until AWS Support
verifies them. CloudFormation rolled the stack back cleanly, and the pipeline was unaffected.
Amplify Hosting gives the same outcome (HTTPS CDN, SPA routing, same-origin API) without that
verification step, so it replaced CloudFront. To switch back later, the CloudFront version of
the web section is recorded in this repository's history.

## Alternatives considered
- **S3 static website hosting.** It needs a **public** bucket and has no HTTPS, so it was
  rejected.
- **Cognito sign-in for uploads and "Fetch now" on the site.** Possible later; it adds a user
  pool, tokens and a write API. The public site stays read-only, and writes stay with the
  operator's scripts (`ingest_now.py`, `upload.py`), which use the deploy profile.
- **API Gateway REST API.** More features (usage plans, request validation) at about 3.5× the
  price of HTTP APIs; nothing here needs them.

## Consequences
- The dashboard has a shareable URL and no credentials on the viewer's machine.
- Visitors can see job metadata, reports and rejected-row samples. All of it is public datasets
  or synthetic data. A private deployment would add Amplify basic auth or Cognito.
- Local development is unchanged in spirit: `backend/scripts/local_demo.py` serves the real
  API Lambda code against moto, and Vite proxies `/api` to it.
- Cost: Amplify Hosting, API Gateway and Lambda all stay within free-tier allowances at
  dashboard traffic levels.
