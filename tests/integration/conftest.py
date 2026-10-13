"""moto-backed S3 + DynamoDB + SQS that mirror template.yaml, and helpers to build events."""

import json
import os

import boto3
import pytest
from moto import mock_aws

from etl_processor.app import EtlJob
from etl_processor.config import Settings
from etl_processor.jobs import JobRepository
from etl_processor.storage import S3Storage
from tests.conftest import SCHEMA_DIR

REGION = "ap-south-1"
BUCKET = "etl-pipeline-123456789012-ap-south-1"
TABLE = "etl-jobs-test"


class FakeClock:
    def __init__(self, now: float = 1_791_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def aws():
    os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing",
                      AWS_DEFAULT_REGION=REGION)
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        ddb = boto3.resource("dynamodb", region_name=REGION)
        table = ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "started_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "status-index",
                "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"},
                              {"AttributeName": "started_at", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
                "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            }],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        sqs = boto3.client("sqs", region_name=REGION)
        dlq_url = sqs.create_queue(QueueName="etl-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"]["QueueArn"]
        queue_url = sqs.create_queue(QueueName="etl-jobs", Attributes={
            "VisibilityTimeout": "360",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn,
                                         "maxReceiveCount": "3"})})["QueueUrl"]
        yield {"s3": s3, "table": table, "sqs": sqs, "queue_url": queue_url,
               "dlq_url": dlq_url}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def job(aws, clock):
    settings = Settings(bucket_name=BUCKET, table_name=TABLE, schema_dir=SCHEMA_DIR)
    return EtlJob(settings, S3Storage(aws["s3"], BUCKET), JobRepository(aws["table"], clock))


def upload(s3, path, key):
    with open(path, "rb") as fh:
        s3.put_object(Bucket=BUCKET, Key=key, Body=fh.read())
    head = s3.head_object(Bucket=BUCKET, Key=key)
    return head


def s3_event(key, head) -> dict:
    return {"Records": [{
        "eventVersion": "2.1", "eventSource": "aws:s3", "awsRegion": REGION,
        "eventName": "ObjectCreated:Put",
        "s3": {"bucket": {"name": BUCKET, "arn": f"arn:aws:s3:::{BUCKET}"},
               "object": {"key": key.replace(" ", "+"), "size": head["ContentLength"],
                          "eTag": head["ETag"].strip('"'),
                          "versionId": head.get("VersionId")}},
    }]}


def sqs_event(body: dict, receive_count: int = 1, message_id: str = "msg-1") -> dict:
    return {"Records": [{
        "messageId": message_id, "receiptHandle": "rh", "body": json.dumps(body),
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "eventSource": "aws:sqs",
    }]}


def list_keys(s3, prefix: str) -> list[str]:
    response = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(o["Key"] for o in response.get("Contents", []))


def read_text(s3, key: str) -> str:
    return s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8")
