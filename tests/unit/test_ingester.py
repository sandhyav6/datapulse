"""Public-dataset ingester: config, HTTPS fetch limits, change detection, error isolation."""

import hashlib
import io
import json

import pytest

from ingester import app as ingester
from tests.conftest import ROOT
from tests.integration.conftest import BUCKET

SOURCE = ingester.Source(dataset="world_population", url="https://example.org/pop.csv")


def fixed(body: bytes):
    calls = []

    def fetcher(url, max_bytes):
        calls.append(url)
        return body
    fetcher.calls = calls
    return fetcher


def test_shipped_sources_file_is_valid():
    sources = ingester.load_sources(ROOT / "src" / "sources.json")
    assert [s.dataset for s in sources] == ["global_temperature", "world_population"]
    assert all(s.url.startswith("https://") and s.license for s in sources)
    for source in sources:  # every source has a schema the pipeline can use
        assert (ROOT / "src" / "schemas" / f"{source.dataset}.json").is_file()


@pytest.mark.parametrize("sources, message", [
    ([{"dataset": "a", "url": "http://example.org/a.csv"}], "https"),
    ([{"dataset": "a", "url": "https://x/a.csv"}, {"dataset": "a", "url": "https://x/b.csv"}],
     "only once"),
])
def test_load_sources_rejects_bad_config(tmp_path, sources, message):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": sources}))
    with pytest.raises(ValueError, match=message):
        ingester.load_sources(path)


def test_first_ingest_uploads_with_metadata(aws):
    body = b"Country Name,Country Code,Year,Value\nIndia,IND,2025,1463865525\n"
    result = ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(body))
    assert result["status"] == "uploaded"
    assert result["key"] == "raw/world_population/world_population.csv"
    head = aws["s3"].head_object(Bucket=BUCKET, Key=result["key"])
    assert head["Metadata"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert head["Metadata"]["source-url"] == SOURCE.url
    assert head["ContentType"] == "text/csv"


def test_unchanged_source_is_skipped_without_a_new_version(aws):
    body = b"a,b\n1,2\n"
    ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(body))
    assert ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(body))["status"] == "unchanged"
    versions = aws["s3"].list_object_versions(Bucket=BUCKET, Prefix=SOURCE.key)["Versions"]
    assert len(versions) == 1  # no new object version -> no S3 event -> no new job


def test_changed_source_creates_a_new_version(aws):
    ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(b"a,b\n1,2\n"))
    result = ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(b"a,b\n1,3\n"))
    assert result["status"] == "uploaded" and result["version_id"]
    versions = aws["s3"].list_object_versions(Bucket=BUCKET, Prefix=SOURCE.key)["Versions"]
    assert len(versions) == 2  # raw history is kept by bucket versioning


def test_empty_body_is_an_error(aws):
    with pytest.raises(ingester.IngestError, match="empty"):
        ingester.ingest(SOURCE, aws["s3"], BUCKET, fixed(b"  \n"))


def test_one_failing_source_does_not_stop_the_others(aws):
    good = ingester.Source(dataset="global_temperature", url="https://example.org/t.csv")

    def fetcher(url, max_bytes):
        if "pop" in url:
            raise TimeoutError("publisher timed out")
        return b"Source,Year,Mean\nGCAG,2024-01,1.2\n"

    outcome = ingester.run([SOURCE, good], aws["s3"], BUCKET, fetcher=fetcher)
    assert outcome["summary"] == {"uploaded": 1, "unchanged": 0, "error": 1}
    error = outcome["results"][0]
    assert error["status"] == "error" and error["error_type"] == "TimeoutError"


def test_run_can_target_a_subset(aws):
    fetcher = fixed(b"a\n1\n")
    other = ingester.Source(dataset="global_temperature", url="https://example.org/t.csv")
    outcome = ingester.run([SOURCE, other], aws["s3"], BUCKET, only=["global_temperature"],
                           fetcher=fetcher)
    assert [r["dataset"] for r in outcome["results"]] == ["global_temperature"]
    assert fetcher.calls == [other.url]


def test_handler_fails_the_invocation_when_a_source_fails(aws, monkeypatch, tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [{"dataset": "x", "url": "https://bad/x.csv"}]}))
    monkeypatch.setenv("SOURCES_FILE", str(path))
    monkeypatch.setenv("BUCKET_NAME", BUCKET)

    def broken(url, max_bytes, timeout=30):
        raise OSError("DNS failure")

    monkeypatch.setattr(ingester, "fetch", broken)
    with pytest.raises(ingester.IngestError, match="x"):
        ingester.handler({}, None)


def test_handler_success(aws, monkeypatch, tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [{"dataset": "x", "url": "https://ok/x.csv"}]}))
    monkeypatch.setenv("SOURCES_FILE", str(path))
    monkeypatch.setenv("BUCKET_NAME", BUCKET)
    monkeypatch.setattr(ingester, "fetch", fixed(b"a\n1\n"))
    assert ingester.handler({"datasets": ["x"]}, None)["summary"]["uploaded"] == 1


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, length: str | None = None):
        super().__init__(body)
        self.headers = {"Content-Length": length} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_enforces_https_and_size(monkeypatch):
    with pytest.raises(ingester.IngestError, match="non-https"):
        ingester.fetch("http://example.org/a.csv", 10)

    monkeypatch.setattr(ingester.urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(b"x" * 5, "999999"))
    with pytest.raises(ingester.IngestError, match="limit"):
        ingester.fetch("https://example.org/a.csv", 10)

    monkeypatch.setattr(ingester.urllib.request, "urlopen",
                        lambda req, timeout: FakeResponse(b"x" * 11))  # no Content-Length
    with pytest.raises(ingester.IngestError, match="limit"):
        ingester.fetch("https://example.org/a.csv", 10)

    seen = {}

    def ok(req, timeout):
        seen["agent"] = req.headers.get("User-agent")
        return FakeResponse(b"a,b\n")

    monkeypatch.setattr(ingester.urllib.request, "urlopen", ok)
    assert ingester.fetch("https://example.org/a.csv", 10) == b"a,b\n"
    assert seen["agent"].startswith("serverless-etl-platform-ingester")
