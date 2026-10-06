"""Published layers: rollups preserve counts, tiles cover their cells, provider views stay gated."""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from csb_census import layers
from csb_census.state import State
from tests.synthetic import seeded, step


@pytest.fixture()
def state(con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path) -> State:
    st, source = seeded(con, archive, tmp_path)
    step(con, st, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    return st


def totals(con: duckdb.DuckDBPyConnection, glob: Path) -> tuple[int, int]:
    row = con.sql(f"SELECT sum(n_published), sum(n_unique) FROM '{glob.as_posix()}'").fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def test_every_level_preserves_mapped_totals(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "site-data"
    meta = layers.build(con, state, out)
    want = (meta["mapped_published"], meta["mapped_unique"])
    assert want == (18, 10)  # 8 files, 18 rows; unique: S1-S6, T1-T2, F1-F2
    for res in layers.LEVELS:
        assert totals(con, out / "layers" / f"r{res}" / "*" / "*.parquet") == want
    assert (meta["published"], meta["unique"]) == want


def test_manifest_bboxes_contain_their_cells(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "site-data"
    layers.build(con, state, out)
    manifest = json.loads((out / "layers" / "manifest.json").read_text())
    assert {t["res"] for t in manifest["tiles"]} == {4, 6, 8}
    for tile in manifest["tiles"]:
        w, s, e, n = tile["bbox"]
        for rel in tile["files"]:
            pts = con.sql(
                f"""SELECT h3_cell_to_lng(h3_string_to_h3(h3)), h3_cell_to_lat(h3_string_to_h3(h3))
                    FROM '{(out / rel).as_posix()}'"""
            ).fetchall()
            assert pts and all(w <= x <= e and s <= y <= n for x, y in pts)


def test_provider_views_are_opt_in(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "public"
    layers.build(con, state, out)
    assert not (out / "layers" / "providers").exists()
    series = json.loads((out / "timeseries_month.json").read_text())
    assert "providers" not in series and "providers" not in json.loads(
        (out / "layers/manifest.json").read_text()
    )

    out = tmp_path / "full"
    layers.build(con, state, out, providers=True)
    assert sorted(p.name for p in (out / "layers" / "providers").iterdir()) == ["p1", "p2"]
    series = json.loads((out / "timeseries_month.json").read_text())
    by_provider = sum(r[1] for rows in series["providers"].values() for r in rows)
    assert by_provider == sum(r[1] for r in series["community"])
