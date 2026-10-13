"""Preview the dashboard with NO AWS account: a local moto server plays S3/SQS/DynamoDB.

It creates the same bucket, queues and table as template.yaml, runs the real ingester against
the real public sources in src/sources.json (falling back to bundled fixtures when offline),
runs the real Lambda code over those plus the sales sample files (clean, dirty, a poison file
that fails 3 times, and a second dataset), then starts Streamlit against it.

  pip install "moto[server]"            # once, into your .venv
  python scripts/local_demo.py          # then open http://localhost:8501
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REGION = "ap-south-1"
BUCKET = "etl-pipeline-000000000000-ap-south-1"
TABLE = "etl-jobs-local"


def create_resources(boto3, endpoint: str) -> dict:
    s3 = boto3.client("s3", endpoint_url=endpoint)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    ddb = boto3.resource("dynamodb", endpoint_url=endpoint)
    table = ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"}
                              for n in ("job_id", "status", "started_at")],
        GlobalSecondaryIndexes=[{
            "IndexName": "status-index",
            "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"},
                          {"AttributeName": "started_at", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}}],
        ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5})
    sqs = boto3.client("sqs", endpoint_url=endpoint)
    dlq = sqs.create_queue(QueueName="etl-jobs-dlq-local")["QueueUrl"]
    queue = sqs.create_queue(QueueName="etl-jobs-local")["QueueUrl"]
    return {"s3": s3, "table": table, "sqs": sqs, "queue_url": queue, "dlq_url": dlq}


def run_pipeline(res: dict, schema_dir: Path) -> None:
    from etl_processor.app import EtlJob, handle_event
    from etl_processor.config import Settings
    from etl_processor.jobs import JobRepository
    from etl_processor.storage import S3Storage

    job = EtlJob(Settings(BUCKET, TABLE, schema_dir), S3Storage(res["s3"], BUCKET),
                 JobRepository(res["table"]))
    from ingester import app as ingester

    keys = []
    for source in ingester.load_sources(ROOT / "src" / "sources.json"):
        try:
            result = ingester.ingest(source, res["s3"], BUCKET)
            origin = f"live, {result['bytes']:,} bytes from {source.url}"
        except Exception as exc:  # noqa: BLE001 - offline: use the bundled fixture
            fixture = (ROOT / "tests" / "fixtures" / f"{source.dataset}_sample.csv").read_bytes()
            ingester.ingest(source, res["s3"], BUCKET, lambda url, max_bytes, b=fixture: b)
            origin = f"offline fixture ({type(exc).__name__})"
        print(f"  ingested {source.dataset:<20} {origin}", flush=True)
        keys.append((source.key, 1))

    uploads = [("sales", "clean_small.csv", 1), ("sales", "dirty_small.csv", 1),
               ("sales", "poison.csv", 3), ("returns", "returns_small.csv", 1)]
    for dataset, name, deliveries in uploads:
        key = f"raw/{dataset}/{name}"
        res["s3"].put_object(Bucket=BUCKET, Key=key,
                             Body=(ROOT / "sample_data" / name).read_bytes())
        keys.append((key, deliveries))

    for key, deliveries in keys:
        name = key.rsplit("/", 1)[-1]
        head = res["s3"].head_object(Bucket=BUCKET, Key=key)
        body = {"Records": [{"eventName": "ObjectCreated:Put", "s3": {
            "bucket": {"name": BUCKET},
            "object": {"key": key, "eTag": head["ETag"].strip('"'), "size": head["ContentLength"],
                       "versionId": head.get("VersionId")}}}]}
        for receive in range(1, deliveries + 1):
            event = {"Records": [{"messageId": f"{name}-{receive}", "body": json.dumps(body),
                                  "attributes": {"ApproximateReceiveCount": str(receive)}}]}
            with contextlib.redirect_stdout(io.StringIO()):
                result = handle_event(event, job, "local-demo")
        status = "FAILED -> DLQ" if result["batchItemFailures"] else "SUCCEEDED"
        print(f"  {key:<50} {status}", flush=True)
        if result["batchItemFailures"]:
            res["sqs"].send_message(QueueUrl=res["dlq_url"], MessageBody=json.dumps(body))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5005, help="moto server port")
    parser.add_argument("--seed-only", action="store_true",
                        help="seed the fake AWS and keep it running without starting Streamlit")
    args = parser.parse_args()

    try:
        from moto.server import ThreadedMotoServer
    except ImportError:
        sys.exit('moto server is not installed: pip install "moto[server]"')
    import boto3

    endpoint = f"http://127.0.0.1:{args.port}"
    fake_env = {"AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local",
                "AWS_DEFAULT_REGION": REGION, "AWS_REGION": REGION}
    os.environ.update(fake_env)
    os.environ.pop("AWS_PROFILE", None)

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide per-request access logs
    server = ThreadedMotoServer(port=args.port, verbose=False)
    server.start()
    try:
        res = create_resources(boto3, endpoint)
        with tempfile.TemporaryDirectory() as schemas:
            for schema in (ROOT / "src" / "schemas").glob("*.json"):  # everything the Lambda ships
                shutil.copy(schema, schemas)
            shutil.copy(ROOT / "docs" / "demo" / "returns.json", schemas)  # + the demo dataset
            print(f"Fake AWS running at {endpoint}. Processing sample files:")
            run_pipeline(res, Path(schemas))

        env = {**os.environ, **fake_env, "AWS_ENDPOINT_URL": endpoint, "AWS_PROFILE": "",
               "BUCKET_NAME": BUCKET, "TABLE_NAME": TABLE, "QUEUE_URL": res["queue_url"],
               "DLQ_URL": res["dlq_url"], "LOG_GROUP": "/aws/lambda/etl-processor-local",
               "ATHENA_WORKGROUP": "", "GLUE_DATABASE": "", "INGESTER_FUNCTION": ""}
        if args.seed_only:
            print("Seeded. Press Ctrl+C to stop.")
            server._thread.join()
            return
        print("Starting Streamlit on http://localhost:8501 (Ctrl+C to stop)", flush=True)
        print("Note: uploads in this mode are stored but not processed - there is no Lambda.",
              flush=True)
        app = str(ROOT / "dashboard" / "app.py")
        subprocess.run([sys.executable, "-m", "streamlit", "run", app, "--server.port", "8501"],
                       env=env, cwd=ROOT, check=False)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
