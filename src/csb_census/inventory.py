"""Anonymous listing and download of the public DCDB CSB bucket (NOAA Open Data Dissemination).

Requests reuse one keep-alive HTTPS connection per worker thread: early archive days hold
thousands of small files, where a TLS handshake per file dominated throughput (about 5 MB/s).
"""

import hashlib
import http.client
import io
import logging
import random
import shutil
import threading
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

BUCKET_HOST = "noaa-dcdb-bathymetry-pds.s3.amazonaws.com"
BUCKET_URL = f"https://{BUCKET_HOST}/"
CSV_PREFIX = "csb/csv/"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
RETRIES = 5

log = logging.getLogger(__name__)
_local = threading.local()


def _with_retries[T](action: Callable[[], T], what: str, *, retries: int = RETRIES) -> T:
    """Run ``action``, retrying transient network failures with exponential backoff and jitter.

    A multi-hour backfill makes hundreds of thousands of requests; a single TLS handshake
    timeout must not end the run.
    """
    for attempt in range(retries + 1):
        try:
            return action()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise  # 4xx will not succeed on retry
            if attempt == retries:
                raise
            delay = min(60.0, 2.0**attempt) * (0.5 + random.random())
            log.warning("%s failed (%s); retry %d/%d in %.1fs", what, exc, attempt + 1, retries, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def _reset_connection() -> None:
    conn: http.client.HTTPSConnection | None = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None


def _http_get(path: str, sink: BinaryIO | None, timeout: float) -> bytes:
    """GET ``path`` on the bucket over this thread's keep-alive connection.

    Streams the body into ``sink`` (returning b"") or returns it. Any failure drops the
    connection so the retry starts on a fresh one.
    """
    conn: http.client.HTTPSConnection | None = getattr(_local, "conn", None)
    if conn is None:
        conn = http.client.HTTPSConnection(BUCKET_HOST, timeout=timeout)
        _local.conn = conn
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status >= 400:
            body = resp.read()
            raise urllib.error.HTTPError(
                BUCKET_URL + path.lstrip("/"), resp.status, resp.reason, resp.headers, io.BytesIO(body)
            )
        if sink is None:
            return resp.read()
        shutil.copyfileobj(resp, sink, 1 << 20)
        return b""
    except BaseException:
        _reset_connection()
        raise


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    last_modified: datetime
    etag: str = ""

    @property
    def url(self) -> str:
        return BUCKET_URL + self.key

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


def day_prefix(day: date) -> str:
    """Bucket prefix for one ingest day (the bucket is laid out by ingest date, not collection date)."""
    return f"{CSV_PREFIX}{day:%Y/%m/%d}/"


def list_objects(prefix: str = CSV_PREFIX, *, timeout: float = 60) -> Iterator[S3Object]:
    """Yield every CSV object under ``prefix`` using paginated ListObjectsV2."""
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        path = "/?" + urllib.parse.urlencode(params)

        def page(path: str = path) -> bytes:
            return _http_get(path, None, timeout)

        body = _with_retries(page, f"list {prefix}")
        root = ET.fromstring(body)
        for obj in root.iter(f"{_NS}Contents"):
            key = obj.findtext(f"{_NS}Key") or ""
            if not key.endswith(".csv"):
                continue
            modified = (obj.findtext(f"{_NS}LastModified") or "").replace("Z", "+00:00")
            yield S3Object(
                key,
                int(obj.findtext(f"{_NS}Size") or 0),
                datetime.fromisoformat(modified),
                (obj.findtext(f"{_NS}ETag") or "").strip('"'),
            )
        if root.findtext(f"{_NS}IsTruncated") != "true":
            return
        token = root.findtext(f"{_NS}NextContinuationToken")


def download(
    objects: Sequence[S3Object], dest: Path, *, workers: int = 16, timeout: float = 120
) -> list[Path]:
    """Fetch objects to ``dest`` in parallel; existing files of the right size are kept (resumable)."""
    dest.mkdir(parents=True, exist_ok=True)

    def fetch(obj: S3Object) -> Path:
        path = dest / obj.name
        if path.exists() and path.stat().st_size == obj.size:
            return path
        partial = path.with_suffix(".part")

        def attempt() -> None:
            # Each attempt rewrites the partial file from the start.
            with partial.open("wb") as fh:
                _http_get("/" + urllib.parse.quote(obj.key), fh, timeout)

        _with_retries(attempt, f"get {obj.key}")
        partial.replace(path)
        return path

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, objects))


class ObjectSource(Protocol):
    """Where published CSVs come from: the NOAA bucket in production, a directory in tests and replays."""

    def listing(self, prefix: str) -> Iterator[S3Object]: ...

    def fetch(self, objects: Sequence[S3Object], dest: Path) -> list[Path]: ...


@dataclass
class S3Source:
    workers: int = 16
    timeout: float = 120

    def listing(self, prefix: str) -> Iterator[S3Object]:
        return list_objects(prefix, timeout=self.timeout)

    def fetch(self, objects: Sequence[S3Object], dest: Path) -> list[Path]:
        return download(objects, dest, workers=self.workers, timeout=self.timeout)


@dataclass
class LocalSource:
    """A directory laid out like the bucket (``root/csb/csv/YYYY/MM/DD/*.csv``).

    ETags are content MD5s, as S3 reports for single-part uploads. ``fetched`` records every
    key handed out, so tests can assert what was (and was not) re-read.
    """

    root: Path
    fetched: list[str] | None = None

    def listing(self, prefix: str) -> Iterator[S3Object]:
        for path in sorted(self.root.rglob("*.csv")):
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                stat = path.stat()
                yield S3Object(
                    key,
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    hashlib.md5(path.read_bytes()).hexdigest(),
                )

    def fetch(self, objects: Sequence[S3Object], dest: Path) -> list[Path]:
        dest.mkdir(parents=True, exist_ok=True)
        if self.fetched is None:
            self.fetched = []
        paths = []
        for obj in objects:
            self.fetched.append(obj.key)
            target = dest / obj.name
            shutil.copyfile(self.root / obj.key, target)
            paths.append(target)
        return paths
