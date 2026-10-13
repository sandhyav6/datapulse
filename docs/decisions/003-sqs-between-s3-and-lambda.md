# ADR 003 — SQS between S3 and Lambda

**Status:** accepted

## Context
Every upload to `raw/` must start exactly one ETL job. A burst of 100 uploads must not
overwhelm anything, a failing file must be retried a bounded number of times and then
isolated, and nothing may be silently lost.

## Options
| Option | Buffering | Retries | Failure isolation | Concurrency control |
|---|---|---|---|---|
| **S3 → SQS → Lambda** (chosen) | queue holds messages up to 14 days | visibility timeout, `maxReceiveCount: 3` | DLQ + redrive | `ScalingConfig.MaximumConcurrency` |
| S3 → Lambda (direct, async invoke) | internal async queue, not visible or controllable | 2 retries, fixed backoff | on-failure destination | only reserved concurrency (often unavailable to new accounts) |
| S3 → EventBridge → Lambda | none beyond EventBridge retries | 24 h retry policy | DLQ on the target | same as direct |
| S3 → EventBridge → SQS → Lambda | as chosen | as chosen | as chosen | as chosen, plus one extra hop |

## Decision
S3 sends `s3:ObjectCreated:*` notifications, filtered to prefix `raw/` and suffix `.csv`, to a
standard SQS queue. Lambda consumes it with batch size 1, `ReportBatchItemFailures`, and
`MaximumConcurrency: 2`. The queue redrives to a DLQ after 3 receives.

- **Buffering.** Bursts wait in the queue, visible as `ApproximateNumberOfMessagesVisible`
  and alarmed on by age (`QueueAgeAlarm`).
- **Retries.** A failed message reappears after the 360 s visibility timeout (≥ 6× the 60 s
  function timeout, per AWS guidance).
- **Isolation.** Poison messages move to the DLQ, which `DlqDepthAlarm` watches, and
  `start-message-move-task` redrives them after a fix.
- **Back-pressure without reserved concurrency.** `MaximumConcurrency` caps the event source
  mapping at 2 concurrent invocations, so a burst never exhausts the account's unreserved
  concurrency pool. Many new accounts cannot set reserved concurrency at all.

## Why not EventBridge
One bucket feeds one consumer. EventBridge adds value when several consumers need
content-based routing, cross-account delivery or archive/replay. None of those is a
requirement here. Even with EventBridge we would still put SQS in front of Lambda for the DLQ
and concurrency control, so it would add a hop and a service without a new capability.

## Consequences
- Delivery is at-least-once (S3 and SQS standard can both duplicate), so the consumer must be
  idempotent. See ADR 001.
- The queue policy must allow `s3.amazonaws.com` to `SendMessage`, conditioned on the bucket
  ARN and account. Without it S3 fails silently when it creates the notification.
- S3 sends an `s3:TestEvent` message when the notification is created; the handler ignores it.
- All SQS usage (including empty polls) stays far inside the 1M requests/month always-free
  allowance.
