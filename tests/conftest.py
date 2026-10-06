"""Shared fixtures: a fresh DuckDB connection and the synthetic archive on disk."""

from pathlib import Path

import duckdb
import pytest

from csb_census import pipeline
from tests.synthetic import build_archive


@pytest.fixture()
def con() -> duckdb.DuckDBPyConnection:
    return pipeline.connect(memory_limit="1GB", threads=2)


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    return build_archive(tmp_path / "bucket")
