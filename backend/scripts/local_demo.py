"""Run the whole platform locally with NO AWS account.

A local moto server plays S3, SQS and DynamoDB. The script ingests the real public datasets in
src/sources.json (bundled fixtures if offline), runs the real processor Lambda code over them and
the sales sample files, then serves the real API Lambda code on http://localhost:8000.

  python scripts/local_demo.py              # from backend/, then in frontend/: npm run dev

The React dev server proxies /api to http://localhost:8000 by default.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
    from ingester import app as ingester

    job = EtlJob(Settings(BUCKET, TABLE, schema_dir), S3Storage(res["s3"], BUCKET),
                 JobRepository(res["table"]))
    keys = []
    for source in ingester.load_sources(ROOT / "src" / "sources.json"):
        try:
            result = ingester.ingest(source, res["s3"], BUCKET)
            origin = f"live, {result['bytes']:,} bytes"
        except Exception as exc:  # noqa: BLE001 - offline: use the bundled fixture
            fixture = (ROOT / "tests" / "fixtures" / f"{source.dataset}_sample.csv").read_bytes()
            ingester.ingest(source, res["s3"], BUCKET, lambda url, max_bytes, b=fixture: b)
            origin = f"offline fixture ({type(exc).__name__})"
        print(f"  ingested {source.dataset:<20} {origin}", flush=True)
        keys.append((source.key, 1))

    for dataset, name, deliveries in [("sales", "clean_small.csv", 1),
                                      ("sales", "dirty_small.csv", 1),
                                      ("sales", "poison.csv", 3),
                                      ("returns", "returns_small.csv", 1)]:
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


def serve_api(port: int) -> None:
    """Serve the API Lambda handler over plain HTTP, the way API Gateway would invoke it."""
    from api import app as api

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            resp = api.handler({"rawPath": url.path, "queryStringParameters": query or None,
                                "requestContext": {"http": {"method": "GET"}}}, None)
            body = resp["body"].encode("utf-8")
            self.send_response(resp["statusCode"])
            for name, value in resp["headers"].items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # quiet access log
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"API (the real Lambda code) on http://localhost:{port}/api/overview", flush=True)
    print("Now run the React app:  cd frontend && npm run dev   ->  http://localhost:5173",
          flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--moto-port", type=int, default=5005)
    parser.add_argument("--api-port", type=int, default=8000)
    args = parser.parse_args()
    try:
        from moto.server import ThreadedMotoServer
    except ImportError:
        sys.exit('moto server is not installed: pip install "moto[server]"')
    import boto3

    endpoint = f"http://127.0.0.1:{args.moto_port}"
    os.environ.update(AWS_ACCESS_KEY_ID="local", AWS_SECRET_ACCESS_KEY="local",
                      AWS_DEFAULT_REGION=REGION, AWS_REGION=REGION)
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        os.environ.pop(name, None)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = ThreadedMotoServer(port=args.moto_port, verbose=False)
    server.start()
    try:
        res = create_resources(boto3, endpoint)
        with tempfile.TemporaryDirectory() as schemas:
            for schema in (ROOT / "src" / "schemas").glob("*.json"):
                shutil.copy(schema, schemas)
            print(f"Fake AWS at {endpoint}. Processing data:", flush=True)
            run_pipeline(res, Path(schemas))
        os.environ.update(AWS_ENDPOINT_URL=endpoint, BUCKET_NAME=BUCKET, TABLE_NAME=TABLE,
                          QUEUE_URL=res["queue_url"], DLQ_URL=res["dlq_url"], STAGE="local",
                          INGEST_SCHEDULE_TEXT="cron(30 6 * * ? *) (Asia/Kolkata)")
        serve_api(args.api_port)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
