"""The operator scripts must agree with the Lambda on job IDs and event shape."""

import json
import sys

import pytest

from etl_processor.app import make_job_id, parse_s3_records
from tests.conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import _stack  # noqa: E402
import generate_sample  # noqa: E402
import replay_event  # noqa: E402


def test_script_job_id_matches_lambda():
    assert _stack.job_id_for("b", "raw/sales/x.csv", '"abc123"') == make_job_id(
        "b", "raw/sales/x.csv", "abc123")


def test_replay_event_is_parsed_like_a_real_notification():
    head = {"ContentLength": 10, "ETag": '"e1"', "VersionId": "v1"}
    event = replay_event.s3_event("bucket", "raw/sales/my file.csv", head, "ap-south-1")
    [obj] = parse_s3_records(json.dumps(event))
    assert obj == {"bucket": "bucket", "key": "raw/sales/my file.csv", "etag": "e1",
                   "version_id": "v1", "size": 10}


def test_generator_is_deterministic(tmp_path):
    a = generate_sample.main(["--rows", "200", "--seed", "3", "--out", str(tmp_path / "a.csv")])
    b = generate_sample.main(["--rows", "200", "--seed", "3", "--out", str(tmp_path / "b.csv")])
    assert a == b
    assert (tmp_path / "a.csv").read_bytes() == (tmp_path / "b.csv").read_bytes()
    assert sum(a["rejection_reasons"].values()) == a["rejected"] == 10


def test_generator_rejects_bad_ratio(tmp_path):
    with pytest.raises(SystemExit):
        generate_sample.main(["--bad-ratio", "1.5", "--out", str(tmp_path / "x.csv")])
