# ADR 001 — Idempotent job processing with a DynamoDB lease

**Status:** accepted

## Context
S3 event notifications and SQS standard queues are both *at-least-once*. The same upload can
reach the Lambda twice (duplicate notification, SQS redelivery, an operator replay, a DLQ
redrive), and an attempt can die halfway through writing outputs. Reprocessing must never
produce duplicate rows or double-counted totals, and a dead attempt must not block the job
forever.

## Decision
1. **Deterministic job identity.** `job_id = sha256(bucket/key/etag)[:16]`. Same object
   version → same job, a new upload of the key → new job. The handler reads the exact
   `versionId` from the event, so the job always processes the bytes that triggered it.
2. **Deterministic outputs.** Every output key is derived from `job_id` (and the
   `ingest_date` fixed at first claim), so a retry *overwrites* the same objects.
3. **One conditional write claims the job.** `UpdateItem` with
   `attribute_not_exists(job_id) OR status = FAILED OR (status = PROCESSING AND lease_expires_at < now)`
   sets `PROCESSING`, a 300 s lease, a unique `attempt_id`, and atomically `ADD attempts 1`.
   On `ConditionalCheckFailedException` a consistent read decides:
   `SUCCEEDED` → `ALREADY_SUCCEEDED` (log `duplicate_delivery`, delete the message);
   anything else → `IN_PROGRESS_ELSEWHERE` (batch item failure, SQS retries later).
4. **Ownership-checked completion.** `mark_succeeded` / `mark_failed` require
   `status = PROCESSING AND attempt_id = <mine>`, so a late attempt cannot overwrite a newer
   result.
5. **Lease < visibility timeout.** 300 s lease < 360 s SQS visibility timeout, so when a crashed
   attempt's message reappears the lease has always expired. Handled errors release the lease
   immediately (`lease_expires_at = 0`) so the next delivery can claim at once.

`UpdateItem` is used instead of the `PutItem` in the original plan because a claim must
increment `attempts` and preserve `ingest_date`/`first_seen_at` across retries; `PutItem`
replaces the whole item and cannot do either. It is still a single conditional write.

## Alternatives considered
- **Powertools for AWS Lambda idempotency utility.** Solid, but it caches the handler's
  *return value* keyed on the payload; here the payload differs between a real notification
  and a replay, and the job state is also what the dashboard reads. It adds a dependency the
  "stdlib + boto3 only" rule forbids.
- **S3 existence checks** ("skip if `reports/<job_id>.json` exists"). Not atomic: two concurrent
  attempts both see "missing" and both run. Also cannot represent "in progress" or "failed".
- **SQS FIFO queues with content-based deduplication.** S3 cannot send event notifications to
  FIFO queues, and FIFO dedup only covers a 5-minute window — it would not stop a redrive or a
  replay an hour later.

## Consequences
- Duplicates cost one DynamoDB write + one read, then exit; no S3 work is repeated.
- The item tells the full story: `attempts`, `first_seen_at`, `started_at`, `error_type`.
- A redriven FAILED job is reclaimed in place (attempts 3 → 4) instead of creating a new job.
- Verified by `tests/integration/test_pipeline_e2e.py`: replays, crash-after-upload,
  hard-crash lease expiry, late-attempt overwrite and redrive recovery.
