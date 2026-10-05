"""Retry behaviour of the S3 client, with the network and the clock faked."""

import http.client
import io
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from csb_census import inventory
from csb_census.inventory import S3Object, download


class FlakyNetwork:
    """Fails the first ``failures`` calls with ``error``, then serves ``body``."""

    def __init__(self, failures: int, error: Exception, body: bytes = b"a,b\n1,2\n") -> None:
        self.failures, self.error, self.body, self.calls = failures, error, body, 0

    def __call__(self, url: str, timeout: float = 0) -> Any:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return io.BytesIO(self.body)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)


def obj(size: int = 8) -> S3Object:
    return S3Object("csb/csv/2026/09/29/x_pointData.csv", size, datetime(2026, 9, 29, tzinfo=UTC))


def test_transient_timeout_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(2, urllib.error.URLError(TimeoutError(60, "Operation timed out")))
    monkeypatch.setattr("urllib.request.urlopen", net)
    [path] = download([obj()], tmp_path)
    assert net.calls == 3
    assert path.read_bytes() == net.body
    assert not path.with_suffix(".part").exists()


def test_dropped_connection_mid_body_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(1, http.client.IncompleteRead(b"a,b\n"))
    monkeypatch.setattr("urllib.request.urlopen", net)
    [path] = download([obj()], tmp_path)
    assert net.calls == 2
    assert path.read_bytes() == net.body


def test_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(99, TimeoutError(60, "Operation timed out"))
    monkeypatch.setattr("urllib.request.urlopen", net)
    with pytest.raises(TimeoutError):
        download([obj()], tmp_path)
    assert net.calls == inventory.RETRIES + 1


def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    err = urllib.error.HTTPError("u", 403, "Forbidden", None, None)  # type: ignore[arg-type]
    net = FlakyNetwork(99, err)
    monkeypatch.setattr("urllib.request.urlopen", net)
    with pytest.raises(urllib.error.HTTPError):
        download([obj()], tmp_path)
    assert net.calls == 1


def test_complete_file_is_not_refetched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    net = FlakyNetwork(0, TimeoutError())
    monkeypatch.setattr("urllib.request.urlopen", net)
    (tmp_path / "x_pointData.csv").write_bytes(b"12345678")
    download([obj(size=8)], tmp_path)
    assert net.calls == 0
