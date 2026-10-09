"""Underway time: minute classing, the incremental run, reconcile, the seed and the published layers."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from csb_census import incremental, layers, underway
from csb_census.incremental import AsOfSource
from csb_census.pipeline import read_keyed
from csb_census.state import State
from tests.synthetic import put, seeded, step

V5 = "P1-00000000-0000-4000-8000-000000000005"
START = datetime(2026, 9, 2, 20, 0, tzinfo=UTC)


def soundings(minute: int, lat: float, lon: float = -72.40, per_minute: int = 6) -> list[str]:
    """Soundings every 10 s through one minute at a fixed position (depth varies so keys differ)."""
    t0 = START + timedelta(minutes=minute)
    return [
        f"{lon:.5f},{lat:.5f},{8 + minute + i / 10:.1f},{(t0 + timedelta(seconds=10 * i)):%Y-%m-%dT%H:%M:%SZ}"
        for i in range(per_minute)
    ]


def track() -> list[str]:
    """10 minutes moving north about 333 m a minute (about 11 kn), 10 still, a gap, one lone minute."""
    rows: list[str] = []
    for m in range(10):
        rows += soundings(m, 41.0 + 0.003 * m)
    for m in range(10, 20):
        rows += soundings(m, 41.027)
    rows += soundings(31, 41.027)  # 12 minutes after the last: its own segment
    return rows


def bands(con: duckdb.DuckDBPyConnection, paths: list[Path]) -> dict[str, int]:
    read_keyed(con, paths, "rows")
    con.sql("CREATE OR REPLACE TEMP TABLE keep AS SELECT DISTINCT file, UNIQUE_ID AS unique_id FROM rows")
    got = con.sql(
        f"SELECT band, count(*) FROM ({underway.minutes_sql('rows', 'keep')}) GROUP BY 1"
    ).fetchall()
    return {b: int(n) for b, n in got}


def test_minutes_are_classed_by_speed(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = put(tmp_path, "20260902220000", V5, "P1", track())
    assert bands(con, [path]) == {"underway": 10, "stationary": 10, "isolated": 1}


def test_a_jump_is_a_glitch_not_a_speed(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    rows = soundings(0, 41.0) + soundings(1, 42.0)  # 60 nm in one minute
    path = put(tmp_path, "20260902220000", V5, "P1", rows)
    assert bands(con, [path]) == {"glitch": 2}


def uw_of(con: duckdb.DuckDBPyConnection, state: State, platform: str = V5) -> tuple[int, int, int]:
    parts = [p for p in (state.path("uw_base"), state.path("uw_delta")) if p is not None]
    union = " UNION ALL BY NAME ".join(f"SELECT * FROM '{p.as_posix()}'" for p in parts)
    row = con.sql(
        f"SELECT coalesce(sum(min_underway), 0), coalesce(sum(min_stationary), 0), "
        f"coalesce(sum(min_other), 0) FROM ({union}) WHERE platform = '{platform}'"
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2])


def test_run_records_underway_time_once(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    put(archive, "20260902220000", V5, "P1", track())
    put(archive, "20260902230000", V5, "P1", track())  # a whole-file resend: adds no time
    step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    assert uw_of(con, state) == (10, 10, 1)

    # Removing the original: its minutes come out, and the resend (all duplicates) never had any.
    when = datetime(2026, 9, 3, 13, tzinfo=UTC)
    next(archive.rglob("20260902220000*.csv")).unlink()
    incremental.reconcile(con, state, AsOfSource(source, when), now=when, work=tmp_path / "rec")
    assert uw_of(con, state) == (0, 0, 0)


def test_seed_matches_the_runs_and_resumes(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    put(archive, "20260902220000", V5, "P1", track())
    step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    by_run = uw_of(con, state)
    cache = tmp_path / "minutes"
    first = incremental.seed_underway(con, state, cache, source, work=tmp_path / "seed")
    assert first.classified_files > 0
    assert uw_of(con, state) == by_run
    assert con.sql(f"SELECT count(*) FROM '{state.require('uw_delta').as_posix()}'").fetchone() == (0,)
    again = incremental.seed_underway(con, state, cache, source, work=tmp_path / "seed2")
    assert (again.classified_files, uw_of(con, state)) == (0, by_run)  # all cached: nothing re-read


def test_layers_publish_underway_hours_and_reach(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    put(archive, "20260902220000", V5, "P1", track())
    step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    out = tmp_path / "site-data"
    meta = layers.build(con, state, out, now=datetime(2026, 9, 3, 12, tzinfo=UTC))
    total_underway, total_stationary = con.sql(
        "SELECT sum(min_underway) / 60.0, sum(min_stationary) / 60.0 FROM uw"
    ).fetchone() or (0, 0)
    assert total_underway > 0 and total_stationary > 0
    for res in layers.LEVELS:
        glob = (out / "layers" / f"r{res}" / "*" / "*.parquet").as_posix()
        got = con.sql(f"SELECT sum(underway_h), sum(stationary_h) FROM '{glob}'").fetchone()
        assert got is not None
        assert abs(got[0] - total_underway) < 0.05 and abs(got[1] - total_stationary) < 0.05
    assert 0 <= meta["share_never_underway"] <= 1
    assert meta["platform_days_never_underway"] <= meta["platform_days_logged"]
    window = (out / "layers" / "recent" / "24h" / "r8" / "*" / "*.parquet").as_posix()
    assert (con.sql(f"SELECT sum(underway_h) FROM '{window}'").fetchone() or (0,))[0] > 0

    reach = json.loads((out / "timeseries_month.json").read_text())["reach"]
    covered = con.sql("SELECT count(DISTINCT h3_cell_to_parent(h3, 8)) FROM cells").fetchone()
    assert covered is not None
    assert sum(r[2] for r in reach["rows"]) == covered[0]
    assert sum(r[1] for r in reach["rows"]) > 0
