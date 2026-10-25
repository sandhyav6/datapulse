"""Empty the pipeline bucket and the website bucket (every object VERSION and delete marker),
then print the command that removes the rest of the stack.

A versioned bucket cannot be deleted while any version remains, so `sam delete` fails on it
until this has run. This permanently deletes all data in the bucket.

  python scripts/teardown.py            # asks for confirmation
  python scripts/teardown.py --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, session, stack_outputs  # noqa: E402


def empty_bucket(s3, bucket: str) -> int:
    """Delete all versions and delete markers; returns how many were removed."""
    deleted = 0
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        batch = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                 for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        for start in range(0, len(batch), 1000):
            chunk = batch[start:start + 1000]
            response = s3.delete_objects(Bucket=bucket,
                                         Delete={"Objects": chunk, "Quiet": True})
            errors = response.get("Errors", [])
            if errors:
                raise RuntimeError(f"could not delete {len(errors)} objects: {errors[:3]}")
            deleted += len(chunk)
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    outputs = stack_outputs(sess, args.stack)
    buckets = [b for b in (outputs.get("BucketName"), outputs.get("SiteBucketName")) if b]
    if not args.yes:
        print("This permanently deletes every object (and every version) in:")
        for bucket in buckets:
            print(f"  s3://{bucket}")
        answer = input("Type DELETE to confirm: ")
        if answer.strip() != "DELETE":
            sys.exit("Aborted - nothing deleted.")
    s3 = sess.client("s3")
    for bucket in buckets:
        print(f"Deleted {empty_bucket(s3, bucket)} object versions from {bucket}.")
    print("\nNow delete the stack yourself (CloudFront takes ~5-15 minutes to disable):\n")
    print(f"  sam delete --stack-name {args.stack} --region {args.region} --profile {args.profile}")
    print("\nThen check Billing -> Bills for leftovers, and delete the SAM artifacts bucket "
          "(aws-sam-cli-managed-default) if you no longer use SAM.")


if __name__ == "__main__":
    main()
