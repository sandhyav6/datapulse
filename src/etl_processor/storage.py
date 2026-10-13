"""S3 I/O: streaming CSV reads and deterministic output writes.

Every output key is a pure function of (dataset, job_id, ingest_date), so a retry or a
replayed event overwrites the same objects instead of creating new ones.
"""

from __future__ import annotations

import codecs
import csv
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Schema
from .transformer import serialise_row


class UnreadableFileError(ValueError):
    """The object is not a readable UTF-8 CSV; retrying will not help."""


@dataclass(frozen=True)
class OutputKeys:
    processed: str
    rejected: str
    report: str
    curated: str


def output_keys(dataset: str, job_id: str, ingest_date: str) -> OutputKeys:
    return OutputKeys(
        processed=(f"processed/dataset={dataset}/ingest_date={ingest_date}/"
                   f"job_id={job_id}/part-0000.csv"),
        rejected=f"rejected/dataset={dataset}/job_id={job_id}/rejected.csv",
        report=f"reports/dataset={dataset}/{job_id}.json",
        curated=(f"curated/dataset={dataset}/ingest_date={ingest_date}/"
                 f"job_id={job_id}/part-0000.parquet"),
    )


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


class S3Storage:
    def __init__(self, s3_client, bucket: str):
        self.s3 = s3_client
        self.bucket = bucket

    def iter_csv(self, key: str, version_id: str | None = None) -> Iterator[list[str]]:
        """Yield CSV rows from a streaming GetObject; the file is never fully in memory.

        Passing the version from the S3 event pins the job to exactly the bytes that were
        uploaded, even if the key is overwritten before the job runs or is retried.
        """
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        body = self.s3.get_object(**kwargs)["Body"]
        text = codecs.getreader("utf-8-sig")(body)
        try:
            yield from csv.reader(text)
        except UnicodeDecodeError as exc:
            raise UnreadableFileError(f"file is not valid UTF-8: {exc.reason}") from exc
        except csv.Error as exc:
            raise UnreadableFileError(f"unparseable CSV: {exc}") from exc
        finally:
            body.close()

    def upload_file(self, path: Path, key: str, content_type: str) -> str:
        self.s3.upload_file(str(path), self.bucket, key, ExtraArgs={"ContentType": content_type})
        return s3_uri(self.bucket, key)

    def put_json(self, key: str, payload: dict) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, ContentType="application/json",
                           Body=json.dumps(payload, indent=2, default=str).encode("utf-8"))
        return s3_uri(self.bucket, key)


class JobOutputs:
    """Buffers clean and rejected rows to local temp files (Lambda /tmp), then uploads them."""

    def __init__(self, schema: Schema, workdir: Path):
        self.schema = schema
        self.processed_path = workdir / "processed.csv"
        self.rejected_path = workdir / "rejected.csv"
        self.parquet_path = workdir / "part-0000.parquet"
        self._processed_fh = open(self.processed_path, "w", newline="", encoding="utf-8")
        self._rejected_fh = open(self.rejected_path, "w", newline="", encoding="utf-8")
        self._processed = csv.writer(self._processed_fh)
        self._rejected = csv.writer(self._rejected_fh)
        self._processed.writerow(schema.output_columns)

    def on_header(self, header: list[str], _result) -> None:
        self._rejected.writerow([*header, "reasons"])

    def write_accepted(self, row: dict[str, Any]) -> None:
        self._processed.writerow(serialise_row(row))

    def write_rejected(self, values: list[str], reasons: list[str]) -> None:
        self._rejected.writerow([*values, ";".join(reasons)])

    def close(self) -> None:
        self._processed_fh.close()
        self._rejected_fh.close()

    def write_parquet(self) -> Path:
        """Convert the clean CSV to snappy Parquet (needs the AWS SDK for pandas layer)."""
        import pyarrow as pa  # noqa: PLC0415 - optional dependency, only present with the layer
        import pyarrow.csv as pacsv
        import pyarrow.parquet as pq

        types = {name: arrow_type(spec.type, pa, spec.scale)
                 for name, spec in self.schema.columns.items()}
        types.update({name: pa.decimal128(18, 2) for name in self.schema.derived})
        table = pacsv.read_csv(
            self.processed_path,
            convert_options=pacsv.ConvertOptions(column_types=types, strings_can_be_null=True,
                                                 quoted_strings_can_be_null=False),
        )
        pq.write_table(table, self.parquet_path, compression="snappy")
        return self.parquet_path


def arrow_type(column_type: str, pa, scale: int = 2):
    return {
        "string": pa.string(),
        "date": pa.date32(),
        "integer": pa.int64(),
        "decimal": pa.decimal128(18, scale),
    }[column_type]
