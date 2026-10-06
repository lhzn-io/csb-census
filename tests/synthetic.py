"""A synthetic archive reproducing every duplicate pattern seen in the real one, plus census helpers."""

import os
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from csb_census import incremental, pipeline
from csb_census.incremental import AsOfSource
from csb_census.inventory import LocalSource
from csb_census.state import State

HEADER = "UNIQUE_ID,FILE_UUID,LON,LAT,DEPTH,TIME,PLATFORM_NAME,PROVIDER"
V1 = "P1-00000000-0000-4000-8000-000000000001"
V2 = "P1-00000000-0000-4000-8000-000000000002"
V9 = "P2-00000000-0000-4000-8000-000000000009"

S1 = "-72.10,41.20,10.5,2026-08-30T10:00:00Z"
S2 = "-72.11,41.21,11.0,2026-08-30T10:00:01Z"
S3 = "-72.12,41.22,11.5,2026-08-30T10:00:02Z"
S4 = "-72.13,41.23,12.0,2026-08-30T10:00:03Z"
S5 = "-72.14,41.24,12.5,2026-08-30T10:00:04Z"
S6 = "-72.15,41.25,13.0,2026-08-30T10:00:05Z"
T1 = "-72.10,41.20,20.0,2026-08-30T10:00:02Z"  # provider P2, same area and time as P1's files
T2 = "-72.11,41.21,21.0,2026-08-30T10:00:03Z"
F1 = "-72.30,41.10,5.0,2026-08-31T08:00:00Z"
F2 = "-72.31,41.11,5.5,2026-08-31T08:00:01Z"

CUTOFF = datetime(2026, 9, 2, tzinfo=UTC)


def put(root: Path, stamp: str, vessel: str, provider: str, soundings: list[str]) -> Path:
    """Write one published file at its bucket key, with mtime = its ingest stamp."""
    day = root / "csb" / "csv" / stamp[:4] / stamp[4:6] / stamp[6:8]
    day.mkdir(parents=True, exist_ok=True)
    name = f"{stamp}000000_{vessel[-12:]}_pointData.csv"
    path = day / name
    rows = [f"{vessel},{name[:-14]},{s},Vessel,{provider}" for s in soundings]
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    ts = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC).timestamp() + 60
    os.utime(path, (ts, ts))
    return path


def build_archive(root: Path) -> Path:
    """Eight published files A..H (see each line)."""
    put(root, "20260901000000", V1, "P1", [S1, S2, S3])  # A
    put(root, "20260901010000", V1, "P1", [S3, S4])  # C: partial overlap with A
    put(root, "20260902000000", V2, "P1", [S4, S5])  # D: S4 again, under another platform ID
    put(root, "20260902010000", V9, "P2", [T1, T2])  # E: other provider, same place and time
    put(root, "20260903000000", V1, "P1", [S1, S2, S3])  # B: whole-file resend of A
    put(root, "20260903010000", V9, "P2", [F1, F2])  # F
    put(root, "20260903010001", V9, "P2", [F1, F2])  # G: double ingest of F, one second later
    put(root, "20260903020000", V1, "P1", [S2, S6])  # H: partial overlap with A
    return root


def all_csvs(root: Path, before: datetime | None = None) -> list[Path]:
    paths = sorted(root.rglob("*.csv"))
    if before is None:
        return paths
    return [p for p in paths if datetime.strptime(p.name[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC) < before]


def backfill(
    con: duckdb.DuckDBPyConnection, root: Path, out: Path, *, before: datetime | None = None
) -> Path:
    """Full backfill of everything, optionally re-ranked as of ``before`` into ``out/asof``."""
    if not (out / "stage").exists():
        pipeline.stage(con, all_csvs(root), out, "all")
    dest = out / "asof" if before else out
    naive = before.replace(tzinfo=None) if before else None
    for month in pipeline.staged_months(out):
        pipeline.rank(con, out, month, before=naive, dest=dest)
    pipeline.finalize(con, out, before=naive, dest=dest)
    return dest


def per_file(con: duckdb.DuckDBPyConnection, index: Path) -> dict[str, tuple[int, int, int, int]]:
    cols = {c for (c, *_) in con.sql(f"DESCRIBE SELECT * FROM '{index.as_posix()}'").fetchall()}
    live = "WHERE status = 'live'" if "status" in cols else ""
    rows = con.sql(
        f"""SELECT left(file, 14), sum(n_rows), sum(n_unique), sum(n_dup_resend), sum(n_dup_cross_id)
            FROM '{index.as_posix()}' {live} GROUP BY 1"""
    ).fetchall()
    return {r[0]: (int(r[1]), int(r[2]), int(r[3]), int(r[4])) for r in rows}


def cells_of_state(con: duckdb.DuckDBPyConnection, state: State) -> dict[int, tuple[int, int]]:
    parts = [state.require("cells_base"), state.require("cells_delta")]
    union = " UNION ALL BY NAME ".join(f"SELECT * FROM '{p.as_posix()}'" for p in parts)
    rows = con.sql(f"SELECT h3_r9, sum(n_rows), sum(n_unique) FROM ({union}) GROUP BY 1").fetchall()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows if r[0] is not None}


def cells_of_backfill(con: duckdb.DuckDBPyConnection, dest: Path) -> dict[int, tuple[int, int]]:
    glob = (dest / "rank" / "cells_r9" / "*.parquet").as_posix()
    rows = con.sql(f"SELECT h3_r9, sum(n_rows), sum(n_unique) FROM '{glob}' GROUP BY 1").fetchall()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows if r[0] is not None}


Vday = tuple[str, str, int]  # (platform, collection day, H3 r8)

_VDAY_COUNTS = "GROUP BY 1, 2, 3 HAVING sum(n_rows - n_dup_cross_id) > 0"


def vdays_of_state(con: duckdb.DuckDBPyConnection, state: State) -> set[Vday]:
    parts = [state.require("vdays_base")] + ([p] if (p := state.path("vdays_delta")) else [])
    union = " UNION ALL BY NAME ".join(f"SELECT * FROM '{p.as_posix()}'" for p in parts)
    return set(con.sql(f"SELECT platform, day, h3_r8 FROM ({union}) {_VDAY_COUNTS}").fetchall())


def vdays_of_backfill(con: duckdb.DuckDBPyConnection, dest: Path) -> set[Vday]:
    glob = (dest / "rank" / "vdays_r8" / "*.parquet").as_posix()
    return set(con.sql(f"SELECT platform, day, h3_r8 FROM '{glob}' {_VDAY_COUNTS}").fetchall())


def r8(con: duckdb.DuckDBPyConnection, sounding: str) -> int:
    lon, lat = sounding.split(",")[:2]
    row = con.sql(f"SELECT h3_latlng_to_cell({lat}, {lon}, 8)").fetchone()
    assert row is not None
    return int(row[0])


def seeded(con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path) -> tuple[State, LocalSource]:
    asof = backfill(con, archive, tmp_path / "bf", before=CUTOFF)
    source = LocalSource(archive)
    state = State(tmp_path / "state")
    incremental.seed(con, asof, state, AsOfSource(source, CUTOFF), work=tmp_path / "w-seed")
    return state, source


def step(
    con: duckdb.DuckDBPyConnection, state: State, source: LocalSource, when: datetime, tmp: Path
) -> incremental.RunResult:
    return incremental.run(
        con, state, AsOfSource(source, when), now=when, run_id=f"{when:%Y%m%dT%H}", work=tmp / f"w{when:%d%H}"
    )
