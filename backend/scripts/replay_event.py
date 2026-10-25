"""Resend the S3 notification for an existing object straight to the job queue.

This is the idempotency demo: the job already SUCCEEDED, so every replay must log
`duplicate_delivery` and leave the totals and the S3 object count unchanged.

  python scripts/replay_event.py raw/sales/dirty_small.csv --times 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, job_id_for, session, stack_outputs  # noqa: E402


def s3_event(bucket: str, key: str, head: dict, region: str) -> dict:
    return {"Records": [{
        "eventVersion": "2.1", "eventSource": "aws:s3", "awsRegion": region,
        "eventTime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "eventName": "ObjectCreated:Put",
        "s3": {"s3SchemaVersion": "1.0",
               "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
               "object": {"key": key.replace(" ", "+"), "size": head["ContentLength"],
                          "eTag": head["ETag"].strip('"'),
                          **({"versionId": head["VersionId"]} if head.get("VersionId") else {})}},
    }]}


def object_count(s3, bucket: str, prefixes: list[str]) -> int:
    total = 0
    for prefix in prefixes:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            total += page.get("KeyCount", 0)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("key", help="object key, e.g. raw/sales/dirty_small.csv")
    parser.add_argument("--times", type=int, default=1)
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    bucket = outputs["BucketName"]
    s3, sqs = sess.client("s3"), sess.client("sqs")
    head = s3.head_object(Bucket=bucket, Key=args.key)
    job_id = job_id_for(bucket, args.key, head["ETag"])
    table = sess.resource("dynamodb").Table(outputs["TableName"])
    prefixes = ["processed/", "rejected/", "reports/"]

    before = table.get_item(Key={"job_id": job_id}).get("Item", {})
    count_before = object_count(s3, bucket, prefixes)
    print(f"job_id {job_id}: status={before.get('status')} attempts={before.get('attempts')} "
          f"rows_accepted={before.get('rows_accepted')} output objects={count_before}")

    body = json.dumps(s3_event(bucket, args.key, head, args.region))
    for i in range(args.times):
        message_id = sqs.send_message(QueueUrl=outputs["QueueUrl"], MessageBody=body)["MessageId"]
        print(f"replay {i + 1}/{args.times}: sent message {message_id}")

    print("Waiting 20 s for the replays to be processed...")
    time.sleep(20)
    after = table.get_item(Key={"job_id": job_id}).get("Item", {})
    count_after = object_count(s3, bucket, prefixes)
    print(f"job_id {job_id}: status={after.get('status')} attempts={after.get('attempts')} "
          f"rows_accepted={after.get('rows_accepted')} output objects={count_after}")
    unchanged = (before.get("attempts"), before.get("rows_accepted"), count_before) == (
        after.get("attempts"), after.get("rows_accepted"), count_after)
    print("IDEMPOTENT: nothing changed" if unchanged else "CHANGED - investigate the logs")
    print("Check the logs: Logs Insights query 'etl-pipeline-dev/recent-jobs' shows "
          "duplicate_delivery lines.")


if __name__ == "__main__":
    main()
