"""Regional analysis over a small square around the synthetic archive's P1 track."""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from csb_census import lis
from csb_census.state import State
from tests.synthetic import backfill, seeded, step

# A box around S1..S6 (-72.15..-72.10, 41.20..41.25) that excludes F1/F2 (-72.30, 41.10).
SQUARE = {
    "type": "Feature",
    "properties": {"name": "test square"},
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[-72.17, 41.18], [-72.08, 41.18], [-72.08, 41.27], [-72.17, 41.27], [-72.17, 41.18]]
        ],
    },
}


@pytest.fixture()
def polygon(tmp_path: Path) -> Path:
    path = tmp_path / "square.geojson"
    path.write_text(json.dumps(SQUARE))
    return path


@pytest.fixture()
def state(con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path) -> State:
    st, source = seeded(con, archive, tmp_path)
    step(con, st, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    return st


def test_polygon_wkts_handles_multipolygons(tmp_path: Path) -> None:
    multi = {"type": "MultiPolygon", "coordinates": [SQUARE["geometry"]["coordinates"]] * 2}  # type: ignore[index]
    path = tmp_path / "m.geojson"
    path.write_text(json.dumps(multi))
    wkts = lis.polygon_wkts(path)
    assert len(wkts) == 2 and wkts[0].startswith("POLYGON ((-72.17 41.18")


def test_build_keeps_only_region_cells(
    con: duckdb.DuckDBPyConnection, state: State, polygon: Path, tmp_path: Path
) -> None:
    summary = lis.build(con, state, polygon, tmp_path / "lis")
    # In the box: S1-S6 (P1) and T1-T2 (P2); F1/F2 lie outside it.
    assert summary["all_time"]["unique"] == 8
    assert summary["all_time"]["providers"] == 2
    assert 0 < summary["all_time"]["share_ge1"] <= 1
    assert summary["region_km2"] > 50  # about 7.5 km by 10 km
    cells = con.sql(f"SELECT sum(n_unique) FROM '{(tmp_path / 'lis' / 'r9.parquet').as_posix()}'").fetchone()
    assert cells == (8,)


def test_extract_keeps_originals_with_copy_counts(
    con: duckdb.DuckDBPyConnection, archive: Path, polygon: Path, tmp_path: Path
) -> None:
    out = backfill(con, archive, tmp_path / "bf")
    n = lis.extract(con, out / "stage", polygon, tmp_path / "lis")
    assert n == 8
    soundings = (tmp_path / "lis" / "soundings.parquet").as_posix()
    rows = dict(con.sql(f"SELECT time, n_copies FROM '{soundings}' WHERE provider = 'P1'").fetchall())
    assert rows["2026-08-30T10:00:00Z"] == 2  # S1: in A and its resend B
    assert rows["2026-08-30T10:00:01Z"] == 3  # S2: A, B and H
