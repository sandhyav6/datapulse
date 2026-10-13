"""Empty the pipeline bucket (every object VERSION and delete marker), then print the commands
that remove the rest of the stack.

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
    bucket = stack_outputs(sess, args.stack)["BucketName"]
    if not args.yes:
        answer = input(f"Permanently delete EVERY object version in s3://{bucket}? "
                       "Type the bucket name to confirm: ")
        if answer.strip() != bucket:
            sys.exit("Aborted - nothing deleted.")
    print(f"Deleted {empty_bucket(sess.client('s3'), bucket)} object versions from {bucket}.")
    print("\nNow run these yourself, in order (the policy must be detached before sam delete):\n")
    print("  aws iam detach-user-policy --user-name etl-dashboard --policy-arn "
          "<DashboardPolicyArn> --profile etl-dev")
    print("  aws iam list-access-keys --user-name etl-dashboard --profile etl-dev")
    print("  aws iam delete-access-key --user-name etl-dashboard --access-key-id <key-id> "
          "--profile etl-dev")
    print("  aws iam delete-user --user-name etl-dashboard --profile etl-dev")
    print(f"  sam delete --stack-name {args.stack} --region {args.region} --profile {args.profile}")
    print("\nThen check Billing → Bills for leftovers, and delete the SAM artifacts bucket "
          "(aws-sam-cli-managed-default) if you no longer use SAM.")


if __name__ == "__main__":
    main()
