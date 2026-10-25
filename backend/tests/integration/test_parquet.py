"""V2: OUTPUT_FORMAT=both writes CSV to processed/ and snappy Parquet to curated/."""

import io
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import SAMPLE_DIR, expected_counts
from tests.integration.conftest import BUCKET, list_keys, s3_event, sqs_event, upload

pq = pytest.importorskip("pyarrow.parquet")

from etl_processor.app import handle_event  # noqa: E402


@pytest.mark.parametrize("fmt, has_csv", [("both", True), ("parquet", False)])
def test_parquet_output(aws, job, fmt, has_csv):
    job.settings = replace(job.settings, output_format=fmt)
    key = "raw/sales/dirty_small.csv"
    head = upload(aws["s3"], SAMPLE_DIR / "dirty_small.csv", key)
    assert handle_event(sqs_event(s3_event(key, head)), job, "req") == {"batchItemFailures": []}

    [curated] = list_keys(aws["s3"], "curated/")
    assert curated.startswith("curated/dataset=sales/ingest_date=")
    assert curated.endswith("/part-0000.parquet")
    assert bool(list_keys(aws["s3"], "processed/")) is has_csv

    body = aws["s3"].get_object(Bucket=BUCKET, Key=curated)["Body"].read()
    parquet = pq.ParquetFile(io.BytesIO(body))
    assert parquet.metadata.row_group(0).column(0).compression == "SNAPPY"
    table = parquet.read()
    assert table.num_rows == expected_counts(SAMPLE_DIR / "dirty_small.csv")["accepted"]
    types = {f.name: str(f.type) for f in table.schema}
    assert types == {"transaction_id": "string", "transaction_date": "date32[day]",
                     "product_category": "string", "quantity": "int64",
                     "unit_price": "decimal128(18, 2)", "region": "string",
                     "transaction_amount": "decimal128(18, 2)"}
    first = table.slice(0, 1).to_pylist()[0]
    assert isinstance(first["transaction_date"], date)
    assert isinstance(first["transaction_amount"], Decimal)
