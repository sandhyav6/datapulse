"""Shared helpers for the operator scripts: CLI args, boto3 session and stack outputs."""

from __future__ import annotations

import argparse
import hashlib
import sys
import time

import boto3

DEFAULT_STACK = "serverless-etl-dev"
DEFAULT_PROFILE = "etl-dev"
DEFAULT_REGION = "ap-south-1"


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stack", default=DEFAULT_STACK)
    parser.add_argument("--profile", default=DEFAULT_PROFILE,
                        help="AWS profile (use 'none' for env credentials)")
    parser.add_argument("--region", default=DEFAULT_REGION)


def session(args) -> boto3.Session:
    profile = None if args.profile in (None, "", "none") else args.profile
    return boto3.Session(profile_name=profile, region_name=args.region)


def stack_outputs(sess: boto3.Session, stack: str) -> dict[str, str]:
    try:
        response = sess.client("cloudformation").describe_stacks(StackName=stack)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Cannot read stack {stack!r}: {exc}\nDeploy it first (sam deploy).")
    return {o["OutputKey"]: o["OutputValue"] for o in response["Stacks"][0].get("Outputs", [])}


def job_id_for(bucket: str, key: str, etag: str) -> str:
    """Must match etl_processor.app.make_job_id."""
    return hashlib.sha256(f"{bucket}/{key}/{etag.strip(chr(34))}".encode()).hexdigest()[:16]


def wait_for_job(table, job_id: str, timeout: float = 180, poll: float = 2) -> dict | None:
    """Poll the jobs table until the job leaves PROCESSING (or the timeout passes)."""
    deadline = time.time() + timeout
    item = None
    while time.time() < deadline:
        item = table.get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
        if item and item.get("status") in ("SUCCEEDED", "FAILED"):
            return item
        time.sleep(poll)
    return item
