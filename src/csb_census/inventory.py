"""Anonymous listing and download of the public DCDB CSB bucket (NOAA Open Data Dissemination)."""

import shutil
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

BUCKET_URL = "https://noaa-dcdb-bathymetry-pds.s3.amazonaws.com/"
CSV_PREFIX = "csb/csv/"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


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
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
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
        with urllib.request.urlopen(obj.url, timeout=timeout) as resp, partial.open("wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
        partial.replace(path)
        return path

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, objects))
