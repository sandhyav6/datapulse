"""Run the dataset-ingester now (instead of waiting for the daily schedule) and print what it did.

  python scripts/ingest_now.py                              # every source in src/sources.json
  python scripts/ingest_now.py --datasets world_population

"uploaded" means the content changed and a new pipeline job is starting; "unchanged" means the
sha256 matched the current object, so nothing was written.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stack import add_common_args, session, stack_outputs  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--datasets", nargs="*", help="subset of datasets (default: all)")
    add_common_args(parser)
    args = parser.parse_args()

    sess = session(args)
    function = stack_outputs(sess, args.stack)["IngesterFunctionName"]
    payload = json.dumps({"datasets": args.datasets} if args.datasets else {}).encode()
    print(f"Invoking {function}...")
    response = sess.client("lambda").invoke(FunctionName=function, Payload=payload)
    body = json.loads(response["Payload"].read() or b"{}")
    if response.get("FunctionError"):
        sys.exit(f"Ingestion failed: {body.get('errorMessage')}")
    for result in body.get("results", []):
        extra = (f"{result.get('bytes', 0):,} bytes" if result["status"] != "error"
                 else f"{result.get('error_type')}: {result.get('error_message')}")
        print(f"  {result['dataset']:<22} {result['status']:<10} {extra}")
    print(f"Summary: {body.get('summary')}")


if __name__ == "__main__":
    main()
