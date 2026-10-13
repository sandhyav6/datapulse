# ADR 004 — DynamoDB, not RDS, for job metadata

**Status:** accepted

## Context
The pipeline needs a durable record per job (status, attempts, counts, timings, errors) that:
1. supports an **atomic conditional claim**, the basis of idempotency (ADR 001);
2. answers "jobs with status X, newest first" for the dashboard;
3. costs nothing when idle and stays inside the AWS Free plan.

## Access patterns
| Pattern | Who | Operation |
|---|---|---|
| Claim / finish one job | Lambda | conditional `UpdateItem` on `job_id` |
| Read one job | Lambda, scripts | `GetItem` (consistent) |
| List jobs by status, newest first | dashboard | `Query` on GSI `status-index` (PK `status`, SK `started_at`) |

There are no joins, aggregations or ad-hoc queries. Aggregates live in the per-job report JSON
in S3; ad-hoc SQL is Athena's job in V2.

## Decision
One DynamoDB table, `etl-jobs-<stage>`, partition key `job_id`, with GSI `status-index`
(projection ALL). Provisioned mode at 5 RCU / 5 WCU on the table and on the GSI: 10 of each,
inside the always-free 25/25.

## Why not RDS (PostgreSQL/MySQL)
- **Idle cost and lifecycle.** A DB instance bills by the hour whether or not files arrive. RDS
  free-tier hours are limited and time-boxed, and the Free plan targets credits rather than
  "free forever". DynamoDB at this scale is always free.
- **Networking.** RDS lives in a VPC. Reaching it from Lambda means VPC-attached functions, and
  reaching S3/SQS from there needs NAT gateways (~$32/month) or VPC endpoints, both explicitly
  out of scope.
- **Connections.** Lambda bursts open many short-lived connections. RDS needs RDS Proxy (paid)
  or careful pooling. DynamoDB is an HTTPS API with no connections to manage.
- **The claim is one API call.** A conditional `UpdateItem` gives atomic
  check-and-set with no transaction or locking code.

## Consequences
- Queries are limited to the designed access patterns. A new question means a new GSI or an
  Athena query over the reports, not ad-hoc SQL.
- With a single low-cardinality GSI partition key (`status`), write throughput on that key would
  hot-spot at very high volume (thousands of jobs per second). At file-level granularity this is
  irrelevant. The fix would be a sharded key such as `status#day`.
- Point-in-time recovery is off (it is not free). Job metadata can be rebuilt from the reports
  in S3, and raw files are versioned.
