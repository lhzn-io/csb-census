"""End-to-end census over synthetic files that reproduce each duplicate pattern seen in the archive."""

from pathlib import Path

import duckdb
import pytest

from csb_census import pipeline

HEADER = "UNIQUE_ID,FILE_UUID,LON,LAT,DEPTH,TIME,PLATFORM_NAME,PROVIDER"
V1 = "TEST-00000000-0000-4000-8000-000000000001"
V2 = "TEST-00000000-0000-4000-8000-000000000002"

S1 = "-72.10,41.20,10.5,2026-08-30T10:00:00Z"
S2 = "-72.11,41.21,11.0,2026-08-30T10:00:01Z"
S3 = "-72.12,41.22,11.5,2026-08-30T10:00:02Z"
S4 = "-72.13,41.23,12.0,2026-08-30T10:00:03Z"
S5 = "-72.14,41.24,12.5,2026-08-30T10:00:04Z"
BAD = "999,999,3.0,not-a-time"


def write(dir_: Path, stamp: str, vessel: str, soundings: list[str]) -> Path:
    name = f"{stamp}000000_{vessel[-12:]}_pointData.csv"
    rows = [f"{vessel},{name[:-14]},{s},Test Vessel,TestNode" for s in soundings]
    path = dir_ / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def census(tmp_path_factory: pytest.TempPathFactory) -> tuple[duckdb.DuckDBPyConnection, Path]:
    src = tmp_path_factory.mktemp("src")
    out = tmp_path_factory.mktemp("out")
    # Day 1: original A, then C partially overlapping A (same vessel, 1 h later).
    a = write(src, "20260901000000", V1, [S1, S2, S3])
    c = write(src, "20260901010000", V1, [S3, S4])
    # Day 2: D repeats S4 under a different platform ID, plus new S5 and a malformed row.
    d = write(src, "20260902000000", V2, [S4, S5, BAD])
    # Day 3: B is a byte-for-byte resend of A's soundings, two days later.
    b = write(src, "20260903000000", V1, [S1, S2, S3])

    con = pipeline.connect(memory_limit="1GB", threads=2)
    pipeline.stage(con, [a, c], out, "20260901")
    pipeline.stage(con, [d], out, "20260902")
    pipeline.stage(con, [b], out, "20260903")
    for month in pipeline.staged_months(out):
        pipeline.rank(con, out, month)
    pipeline.finalize(con, out)
    con.sql(f"CREATE VIEW idx AS SELECT * FROM '{(out / 'file_index.parquet').as_posix()}'")
    con.sql(f"CREATE VIEW daily AS SELECT * FROM '{(out / 'rank' / 'daily').as_posix()}/*.parquet'")
    return con, out


def test_totals(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, _ = census
    rows, unique, resend, cross = (
        con.sql(
            "SELECT sum(n_rows), sum(n_unique), sum(n_dup_resend), sum(n_dup_cross_id) FROM idx"
        ).fetchone()
        or ()
    )
    assert (rows, unique, resend, cross) == (11, 6, 4, 1)


def test_map_cells_reconcile_with_index(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, out = census
    cells = (out / "rank" / f"cells_r{pipeline.CELL_RES}").as_posix()
    got = con.sql(
        f"SELECT sum(n_rows), sum(n_unique), sum(n_dup_resend), sum(n_dup_cross_id) FROM '{cells}/*.parquet'"
    ).fetchone()
    want = con.sql(
        "SELECT sum(n_rows), sum(n_unique), sum(n_dup_resend), sum(n_dup_cross_id) FROM idx"
    ).fetchone()
    assert got == want
    unmapped = con.sql(f"SELECT sum(n_rows) FROM '{cells}/*.parquet' WHERE h3_r9 IS NULL").fetchone()
    assert unmapped == (1,)  # the malformed row is counted but has no cell


def test_index_counts_are_integers(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, _ = census
    types = dict(con.sql("SELECT column_name, column_type FROM (DESCRIBE idx)").fetchall())
    assert {types[c] for c in ("n_rows", "n_unique", "n_dup_resend", "n_dup_cross_id")} == {"BIGINT"}


def test_first_ingested_copy_is_canonical(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, _ = census
    got = dict(con.sql("SELECT left(file, 14), n_unique FROM idx").fetchall())
    assert got == {"20260901000000": 3, "20260901010000": 1, "20260902000000": 2, "20260903000000": 0}


def test_whole_file_resend_shares_fingerprint(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, _ = census
    fp = dict(con.sql("SELECT left(file, 14), fingerprint FROM idx").fetchall())
    assert fp["20260901000000"] == fp["20260903000000"]
    assert len({fp["20260901000000"], fp["20260901010000"], fp["20260902000000"]}) == 3


def test_cross_id_duplicate_is_classified(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, _ = census
    row = con.sql(
        "SELECT n_unique, n_dup_resend, n_dup_cross_id FROM idx WHERE left(file, 14) = '20260902000000'"
    ).fetchone()
    assert row == (2, 0, 1)


def test_malformed_rows_are_counted_not_mapped(census: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    con, out = census
    assert "invalid" in pipeline.staged_months(out)
    bad = con.sql("SELECT n_rows, h3_r5, coll_day FROM daily WHERE coll_day IS NULL").fetchall()
    assert bad == [(1, None, None)]


def test_restaging_a_batch_is_idempotent(
    census: tuple[duckdb.DuckDBPyConnection, Path], tmp_path: Path
) -> None:
    con, out = census
    before = con.sql("SELECT count(*) FROM idx").fetchone()
    src = tmp_path / "again"
    src.mkdir()
    b = write(src, "20260903000000", V1, [S1, S2, S3])
    pipeline.stage(con, [b], out, "20260903")
    assert not pipeline.is_done(out, "rank_2026-08")
    pipeline.rank(con, out, "2026-08")
    pipeline.finalize(con, out)
    assert con.sql("SELECT count(*) FROM idx").fetchone() == before
    assert con.sql("SELECT sum(n_unique) FROM idx").fetchone() == (6,)
