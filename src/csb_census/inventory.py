"""Anonymous listing and download of the public DCDB CSB bucket (NOAA Open Data Dissemination)."""

import http.client
import logging
import random
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

BUCKET_URL = "https://noaa-dcdb-bathymetry-pds.s3.amazonaws.com/"
CSV_PREFIX = "csb/csv/"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
RETRIES = 5

log = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    last_modified: datetime

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
        url = BUCKET_URL + "?" + urllib.parse.urlencode(params)

        def page(url: str = url) -> bytes:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body: bytes = resp.read()
                return body

        root = ET.fromstring(_with_retries(page, f"list {prefix}"))
        for obj in root.iter(f"{_NS}Contents"):
            key = obj.findtext(f"{_NS}Key") or ""
            if not key.endswith(".csv"):
                continue
            modified = (obj.findtext(f"{_NS}LastModified") or "").replace("Z", "+00:00")
            yield S3Object(key, int(obj.findtext(f"{_NS}Size") or 0), datetime.fromisoformat(modified))
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
            with urllib.request.urlopen(obj.url, timeout=timeout) as resp, partial.open("wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)

        _with_retries(attempt, f"get {obj.key}")
        partial.replace(path)
        return path

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, objects))
