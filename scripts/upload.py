"""Upload a CSV to s3://<bucket>/raw/<dataset>/ and (optionally) wait for its job.

  python scripts/upload.py sample_data/dirty_small.csv
  python scripts/upload.py sample_data/clean_small.csv --wait
  python scripts/upload.py sample_data/returns_small.csv --dataset returns
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, job_id_for, session, stack_outputs, wait_for_job  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--dataset", default="sales")
    parser.add_argument("--name", help="object name under raw/<dataset>/ (default: file name)")
    parser.add_argument("--wait", action="store_true", help="wait for the job and print it")
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    bucket = outputs["BucketName"]
    key = f"raw/{args.dataset}/{args.name or args.file.name}"
    with open(args.file, "rb") as fh:
        response = sess.client("s3").put_object(Bucket=bucket, Key=key, Body=fh,
                                                ContentType="text/csv")
    job_id = job_id_for(bucket, key, response["ETag"])
    print(f"Uploaded s3://{bucket}/{key}")
    print(f"  version_id: {response.get('VersionId')}")
    print(f"  job_id:     {job_id}")

    if args.wait:
        table = sess.resource("dynamodb").Table(outputs["TableName"])
        item = wait_for_job(table, job_id)
        print(json.dumps(item, indent=2, default=str) if item else "Job not found yet.")


if __name__ == "__main__":
    main()
