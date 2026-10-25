import csv
import json
from pathlib import Path

import pytest

from etl_processor.config import load_dataset_schema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "src" / "schemas"
SAMPLE_DIR = ROOT / "sample_data"


@pytest.fixture
def sales_schema():
    return load_dataset_schema("sales", SCHEMA_DIR)


def read_csv(path: Path) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def expected_counts(csv_path: Path) -> dict:
    return json.loads(csv_path.with_suffix(".expected.json").read_text(encoding="utf-8"))
