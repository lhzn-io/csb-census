"""Incremental census against the synthetic archive: it must agree exactly with a full backfill."""

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from csb_census import incremental
from csb_census.incremental import AsOfSource
from csb_census.inventory import LocalSource, S3Object
from csb_census.state import State
from tests.synthetic import V1, backfill, cells_of_backfill, cells_of_state, per_file, seeded, step


def test_seed_marks_horizon_and_indexes_keys(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, _ = seeded(con, archive, tmp_path)
    assert state.manifest.generation == 1
    assert state.manifest.max_ingested == "2026-09-01T01:00:00"
    keys = con.sql(f"SELECT count(*) FROM '{state.require('file_index').as_posix()}' WHERE key IS NOT NULL")
    assert keys.fetchone() == (2,)


def test_incremental_equals_full_backfill(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    r1 = step(con, state, source, datetime(2026, 9, 2, 12, tzinfo=UTC), tmp_path)
    r2 = step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    assert (r1.new_files, r2.new_files) == (2, 4)

    full = backfill(con, archive, tmp_path / "bf")
    assert per_file(con, state.require("file_index")) == per_file(con, full / "file_index.parquet")
    assert cells_of_state(con, state) == cells_of_backfill(con, full)


def test_whole_file_resend_needs_no_reread(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    step(con, state, source, datetime(2026, 9, 2, 12, tzinfo=UTC), tmp_path)
    r2 = step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    assert r2.fingerprint_groups == 1  # B matched A by fingerprint
    idx = state.require("file_index").as_posix()
    b = con.sql(
        f"SELECT method, dup_of, n_dup_resend FROM '{idx}' WHERE file LIKE '20260903000000%'"
    ).fetchone()
    assert b is not None and b[0] == "fingerprint" and b[1].startswith("20260901000000") and b[2] == 3


def test_partial_overlap_rereads_same_provider_only(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    step(con, state, source, datetime(2026, 9, 2, 12, tzinfo=UTC), tmp_path)
    source.fetched = []
    step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    stamps = {Path(k).name[:14] for k in source.fetched or []}
    assert "20260901000000" in stamps  # A re-read as a candidate for H
    assert "20260902010000" not in stamps  # E belongs to another provider and is never read


def test_rerun_is_a_no_op(con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path) -> None:
    state, source = seeded(con, archive, tmp_path)
    when = datetime(2026, 9, 3, 12, tzinfo=UTC)
    step(con, state, source, when, tmp_path)
    generation = state.manifest.generation
    again = step(con, state, source, when, tmp_path)
    assert again.new_files == 0 and state.manifest.generation == generation


class Exploding(LocalSource):
    def fetch(self, objects: Sequence[S3Object], dest: Path) -> list[Path]:
        raise ConnectionError("network gone mid-run")


def test_crash_before_commit_leaves_state_intact(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    before = state.manifest.to_json()
    when = datetime(2026, 9, 3, 12, tzinfo=UTC)
    with pytest.raises(ConnectionError):
        incremental.run(con, state, AsOfSource(Exploding(archive), when), now=when, run_id="x")
    assert State(state.root).manifest.to_json() == before
    step(con, state, source, when, tmp_path)
    full = backfill(con, archive, tmp_path / "bf")
    assert per_file(con, state.require("file_index")) == per_file(con, full / "file_index.parquet")


def test_cap_spreads_catch_up_over_runs(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    when = datetime(2026, 9, 3, 12, tzinfo=UTC)
    seen = []
    for i in range(10):
        r = incremental.run(con, state, AsOfSource(source, when), now=when, run_id=f"c{i}", max_files=2)
        seen.append(r.new_files)
        if not r.capped and r.new_files == 0:
            break
    assert seen[:3] == [2, 2, 2] and sum(seen) == 6
    full = backfill(con, archive, tmp_path / "bf")
    assert per_file(con, state.require("file_index")) == per_file(con, full / "file_index.parquet")


def test_reconcile_removal_subtracts_cells_and_queues_originals(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    when = datetime(2026, 9, 3, 12, tzinfo=UTC)
    step(con, state, source, when, tmp_path)
    cells_before = sum(v[0] for v in cells_of_state(con, state).values())
    d = next(archive.rglob("20260902000000*.csv"))  # D: 2 rows, holds the original of S5
    d.unlink()
    result = incremental.reconcile(con, state, AsOfSource(source, when), now=when, work=tmp_path / "rec")
    assert (result.removed, result.republished) == (1, 0)
    assert sum(v[0] for v in cells_of_state(con, state).values()) == cells_before - 2
    queue = con.sql(f"SELECT reason FROM '{state.require('queue').as_posix()}'").fetchall()
    assert queue == [("removed original",)]


def test_reconcile_etag_change_republishes(
    con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path
) -> None:
    state, source = seeded(con, archive, tmp_path)
    when = datetime(2026, 9, 3, 12, tzinfo=UTC)
    step(con, state, source, when, tmp_path)
    h = next(archive.rglob("20260903020000*.csv"))
    mtime = h.stat().st_mtime
    h.write_text(h.read_text() + f"{V1},x,-72.16,41.26,13.5,2026-08-30T10:00:06Z,Vessel,P1\n")
    os.utime(h, (mtime, mtime))
    result = incremental.reconcile(con, state, AsOfSource(source, when), now=when, work=tmp_path / "rec")
    assert result.republished == 1
    rerun = step(con, state, source, when, tmp_path)
    assert rerun.new_files == 1 and rerun.n_unique == 2  # S6 and the new sounding; S2 stays a duplicate
