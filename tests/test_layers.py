"""Published layers: rollups preserve counts, tiles cover their cells, provider views stay gated."""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from csb_census import layers
from csb_census.state import State
from tests.synthetic import seeded, step, vdays_of_state


def h3_parent(con: duckdb.DuckDBPyConnection, cell: int, res: int) -> int:
    row = con.sql(f"SELECT h3_cell_to_parent({cell}::UBIGINT, {res})").fetchone()
    assert row is not None
    return int(row[0])


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


def test_vessel_days_roll_up_as_distinct_platform_days(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "site-data"
    meta = layers.build(con, state, out)
    # V1, V2 and V9 on 2026-08-30, V9 again on 2026-08-31: four vessel-days, three platforms.
    assert (meta["vessel_days"], meta["platforms"]) == (4, 3)
    keys = vdays_of_state(con, state)
    for res in layers.LEVELS:
        # Per cell, vessel-days are distinct (platform, day) pairs, so summed over cells they count
        # each pair once per cell it touched.
        want = len({(p, d, h3_parent(con, h, res)) for p, d, h in keys})
        got = con.sql(
            f"SELECT sum(vessel_days) FROM '{(out / f'layers/r{res}/*/*.parquet').as_posix()}'"
        ).fetchone()
        assert got == (want,)


def test_window_layers_hold_only_days_in_the_window(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "site-data"
    layers.build(con, state, out, now=datetime(2026, 9, 6, 12, tzinfo=UTC))
    manifest = json.loads((out / "layers" / "manifest.json").read_text())
    assert set(manifest["windows"]) == {"7d", "30d", "365d"}
    keys = vdays_of_state(con, state)
    for name, days in (("7d", {"2026-08-31"}), ("30d", {"2026-08-30", "2026-08-31"})):
        want = len({(p, d, h3_parent(con, h, 4)) for p, d, h in keys if d in days})
        glob = out / f"layers/recent/{name}/r4/*/*.parquet"
        assert con.sql(f"SELECT sum(vessel_days) FROM '{glob.as_posix()}'").fetchone() == (want,)
    assert {t["res"] for t in manifest["windows"]["365d"]} == {4, 6}

    later = tmp_path / "later"
    layers.build(con, state, later, now=datetime(2026, 12, 1, tzinfo=UTC))
    assert json.loads((later / "layers" / "manifest.json").read_text())["windows"]["7d"] == []


def test_recent_strip_counts_by_publication(
    con: duckdb.DuckDBPyConnection,
    state: State,
    tmp_path: Path,
) -> None:
    out = tmp_path / "site-data"
    layers.build(con, state, out, now=datetime(2026, 9, 3, 12, tzinfo=UTC))
    recent = json.loads((out / "recent.json").read_text())
    strip = recent["strip"]
    # Since 2026-09-02 12:00: B (resend of A), F, G (double ingest of F) and H.
    assert (strip["24h"]["files"], strip["24h"]["published"]) == (4, 9)
    assert strip["24h"]["new_platforms"] == 0
    assert (strip["all"]["files"], strip["all"]["published"], strip["all"]["unique"]) == (8, 18, 10)
    assert strip["7d"]["new_platforms"] == 3
    assert sum(n for _, n in recent["lag_hist"]["all"]) == 8
    assert dict(recent["lag_hist"]["all"])["1-3d"] >= 1  # A: collected 08-30 10:00, published 09-01
    assert sum(r[1] for r in recent["batches"]["rows"]) == 8
    assert [r[0] for r in recent["runs"]["rows"]] == ["20260903T12"]
    assert "providers" not in recent

    full = tmp_path / "full"
    layers.build(con, state, full, providers=True, now=datetime(2026, 9, 3, 12, tzinfo=UTC))
    by = json.loads((full / "recent.json").read_text())["providers"]
    assert sum(p["all"]["published"] for p in by.values()) == 18


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
