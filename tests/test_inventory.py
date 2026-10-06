"""S3 client behavior with the network and the clock faked, plus the local source used in tests."""

import http.client
import io
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest

from csb_census import inventory
from csb_census.inventory import LocalSource, S3Object, download, list_objects


class FlakyNetwork:
    """Fake ``_http_get``: fails the first ``failures`` calls with ``error``, then serves ``body``."""

    def __init__(self, failures: int, error: Exception, body: bytes = b"a,b\n1,2\n") -> None:
        self.failures, self.error, self.body, self.calls = failures, error, body, 0
        self.paths: list[str] = []

    def __call__(self, path: str, sink: BinaryIO | None, timeout: float) -> bytes:
        self.calls += 1
        self.paths.append(path)
        if self.calls <= self.failures:
            raise self.error
        if sink is None:
            return self.body
        sink.write(self.body)
        return b""


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)


def obj(size: int = 8) -> S3Object:
    return S3Object("csb/csv/2026/09/29/x_pointData.csv", size, datetime(2026, 9, 29, tzinfo=UTC))


def test_transient_timeout_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(2, urllib.error.URLError(TimeoutError(60, "Operation timed out")))
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    [path] = download([obj()], tmp_path)
    assert net.calls == 3
    assert path.read_bytes() == net.body
    assert not path.with_suffix(".part").exists()
    assert net.paths[-1] == "/csb/csv/2026/09/29/x_pointData.csv"


def test_dropped_connection_mid_body_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(1, http.client.IncompleteRead(b"a,b\n"))
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    [path] = download([obj()], tmp_path)
    assert net.calls == 2
    assert path.read_bytes() == net.body


def test_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(99, TimeoutError(60, "Operation timed out"))
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    with pytest.raises(TimeoutError):
        download([obj()], tmp_path)
    assert net.calls == inventory.RETRIES + 1


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    err = urllib.error.HTTPError("u", 403, "Forbidden", http.client.HTTPMessage(), io.BytesIO())
    net = FlakyNetwork(99, err)
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    with pytest.raises(urllib.error.HTTPError):
        download([obj()], tmp_path)
    assert net.calls == 1


def test_complete_file_is_not_refetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(0, TimeoutError())
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    (tmp_path / "x_pointData.csv").write_bytes(b"12345678")
    download([obj(size=8)], tmp_path)
    assert net.calls == 0


LISTING = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents><Key>csb/csv/2026/09/29/a_pointData.csv</Key><LastModified>2026-09-29T06:00:01.000Z</LastModified>
    <ETag>&quot;0f343b0931126a20f133d67c2b018a3b&quot;</ETag><Size>42</Size></Contents>
  <Contents><Key>csb/csv/2026/09/29/readme.txt</Key><LastModified>2026-09-29T06:00:01.000Z</LastModified>
    <ETag>&quot;x&quot;</ETag><Size>1</Size></Contents>
</ListBucketResult>"""


def test_listing_reads_etag_and_skips_non_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    net = FlakyNetwork(0, TimeoutError(), body=LISTING)
    monkeypatch.setattr("csb_census.inventory._http_get", net)
    [o] = list(list_objects("csb/csv/2026/09/29/"))
    assert (o.name, o.size, o.etag) == ("a_pointData.csv", 42, "0f343b0931126a20f133d67c2b018a3b")
    assert o.last_modified == datetime(2026, 9, 29, 6, 0, 1, tzinfo=UTC)
    assert net.paths[0].startswith("/?list-type=2&prefix=csb%2Fcsv%2F2026%2F09%2F29%2F")


def test_local_source_mirrors_bucket_layout(tmp_path: Path) -> None:
    day = tmp_path / "csb" / "csv" / "2026" / "09" / "29"
    day.mkdir(parents=True)
    (day / "a_pointData.csv").write_text("h\n1\n")
    src = LocalSource(tmp_path)
    [o] = list(src.listing("csb/csv/2026/09/"))
    assert o.key == "csb/csv/2026/09/29/a_pointData.csv" and len(o.etag) == 32
    [p] = src.fetch([o], tmp_path / "dl")
    assert p.read_text() == "h\n1\n"
    assert src.fetched == [o.key]
