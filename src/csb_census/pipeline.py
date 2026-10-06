"""Archive-wide census: stage (pass 1), rank (pass 2) and finalize.

Definitions (see docs/src/methodology.md):

* A sounding's identity is ``LON|LAT|DEPTH|TIME`` exactly as DCDB renders it in CSV.
  ``UNIQUE_ID`` is deliberately excluded so the same data under two platform IDs is caught.
* The canonical original is the copy in the earliest-ingested file (filename stamp), ties
  broken by file name. Later copies are ``dup_resend`` (same ``UNIQUE_ID`` as the original)
  or ``dup_cross_id`` (different ``UNIQUE_ID``).
* Every exact duplicate shares ``TIME``, so ranking is independent per collection month.

Layout under ``out``::

    stage/soundings/coll_month=YYYY-MM/*.parquet   keyed soundings (pass 1)
    stage/files/<batch>.parquet                     per (file, UNIQUE_ID) metadata and fingerprint
    rank/file_counts/<month>.parquet                per (file, UNIQUE_ID) unique/duplicate counts
    rank/daily/<month>.parquet                      per (provider, collection day, H3 r5) counts
    rank/cells_r9/<month>.parquet                   per (provider, collection month, H3 r9) counts
    rank/vdays_r8/<month>.parquet                   per (provider, platform, collection day, H3 r8) counts
    file_index.parquet                              finalize: metadata joined with counts
    _done/                                          completion markers (makes every step resumable)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

H3_RES = 5  # daily time-series grain
CELL_RES = 9  # map grain; coarser map levels are rolled up from it with h3_cell_to_parent
VDAY_RES = 8  # vessel-day grain: one row per (platform, collection day, cell)

# Single source of truth for identity. Fields are VARCHAR (read_csv all_varchar), so this hashes
# DCDB's own rendering; coalesce keeps a missing field from shifting the separators.
_FIELDS = "concat_ws('|', coalesce(LON, ''), coalesce(LAT, ''), coalesce(DEPTH, ''), coalesce(TIME, ''))"
SOUNDING_KEY = f"md5_number({_FIELDS})"
SOUNDING_KEY_LOW64 = f"md5_number_lower({_FIELDS})"
_TWO_64 = "18446744073709551616::HUGEINT"

# Per-class tallies over a table with a ``class`` column ('unique' | 'dup_resend' | 'dup_cross_id').
TALLIES = """
    count(*) AS n_rows,
    count(*) FILTER (WHERE class = 'unique') AS n_unique,
    count(*) FILTER (WHERE class = 'dup_resend') AS n_dup_resend,
    count(*) FILTER (WHERE class = 'dup_cross_id') AS n_dup_cross_id
"""


def h3_cell(res: int) -> str:
    """H3 cell of a sounding, NULL when its position is unparseable or out of range."""
    return f"""
        CASE WHEN try_cast(LAT AS DOUBLE) BETWEEN -90 AND 90
              AND try_cast(LON AS DOUBLE) BETWEEN -180 AND 180
             THEN h3_latlng_to_cell(try_cast(LAT AS DOUBLE), try_cast(LON AS DOUBLE), {int(res)})
        END
    """


def vday_sql(table: str) -> str:
    """Per (provider, platform, collection day, H3 r8) tallies over a classified table (``class`` column).

    Platform is ``UNIQUE_ID``. Rows without a valid day or position are left out. A vessel-day
    counts when ``n_rows - n_dup_cross_id > 0``, so data repeated under another ID adds no vessel.
    """
    return f"""
        SELECT * FROM (
          SELECT PROVIDER AS provider, UNIQUE_ID AS platform, left(TIME, 10) AS day,
                 {h3_cell(VDAY_RES)} AS h3_r{VDAY_RES}, {TALLIES}
          FROM {table} WHERE coll_month <> 'invalid' GROUP BY ALL
        ) WHERE h3_r{VDAY_RES} IS NOT NULL
    """


def file_meta_sql(table: str) -> str:
    """Per (file, UNIQUE_ID) metadata and fingerprint over a keyed table (see ``read_keyed``)."""
    return f"""
        SELECT file, UNIQUE_ID AS unique_id, any_value(PROVIDER) AS provider,
               count(*) AS n_rows, min(TIME) AS t_min, max(TIME) AS t_max,
               min(try_cast(LON AS DOUBLE)) AS lon_min, max(try_cast(LON AS DOUBLE)) AS lon_max,
               min(try_cast(LAT AS DOUBLE)) AS lat_min, max(try_cast(LAT AS DOUBLE)) AS lat_max,
               (sum({SOUNDING_KEY_LOW64})::HUGEINT % {_TWO_64})::UBIGINT AS fingerprint,
               min(ingested) AS ingested
        FROM {table} GROUP BY file, UNIQUE_ID
    """


def read_keyed(con: duckdb.DuckDBPyConnection, sources: Sequence[Path | str], table: str) -> None:
    """Read DCDB CSVs as text into temp ``table`` with the sounding key, collection month and ingest stamp.

    The one place identity is computed: stage, incremental and LIS all go through here.
    """
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {table} AS
        SELECT *,
               {SOUNDING_KEY} AS key,
               CASE WHEN regexp_matches(TIME, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}')
                    THEN left(TIME, 7) ELSE 'invalid' END AS coll_month,
               try_strptime(left(file, 14), '%Y%m%d%H%M%S') AS ingested
        FROM (
          SELECT UNIQUE_ID, PROVIDER, LON, LAT, DEPTH, TIME,
                 regexp_extract(filename, '[^/\\\\]+$') AS file
          FROM read_csv($sources, header = true, all_varchar = true, filename = true)
        )
        """,
        {"sources": [p.as_posix() if isinstance(p, Path) else p for p in sources]},
    )


def connect(
    *, memory_limit: str = "32GB", threads: int = 8, temp_dir: Path | None = None
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.sql("INSTALL h3 FROM community; LOAD h3;")
    con.sql(f"SET memory_limit = '{memory_limit}'; SET threads = {int(threads)};")
    con.sql("SET preserve_insertion_order = false;")
    if temp_dir is not None:
        temp_dir.mkdir(parents=True, exist_ok=True)
        con.sql(f"SET temp_directory = '{_sql_path(temp_dir)}';")
    return con


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _done(out: Path, name: str) -> Path:
    return out / "_done" / name


def is_done(out: Path, name: str) -> bool:
    return _done(out, name).exists()


def mark_done(out: Path, name: str) -> None:
    marker = _done(out, name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def clear_done(out: Path, name: str) -> None:
    _done(out, name).unlink(missing_ok=True)


@dataclass(frozen=True)
class StageResult:
    batch: str
    files: int
    rows: int
    months: tuple[str, ...]


def stage(con: duckdb.DuckDBPyConnection, sources: list[Path], out: Path, batch: str) -> StageResult:
    """Pass 1: key every sounding in a batch of CSVs; partition by collection month."""
    read_keyed(con, sources, "batch")
    soundings = out / "stage" / "soundings"
    files_dir = out / "stage" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    con.sql(
        f"""
        COPY batch TO '{_sql_path(soundings)}'
        (FORMAT parquet, PARTITION_BY (coll_month), OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'b{batch}_{{i}}')
        """
    )
    con.sql(
        f"COPY ({file_meta_sql('batch')}) TO '{_sql_path(files_dir / f'{batch}.parquet')}' (FORMAT parquet)"
    )
    files, rows = con.sql("SELECT count(DISTINCT file), count(*) FROM batch").fetchone() or (0, 0)
    months = tuple(m for (m,) in con.sql("SELECT DISTINCT coll_month FROM batch ORDER BY 1").fetchall())
    con.sql("DROP TABLE batch")
    # Any month this batch wrote into must be ranked again.
    for month in months:
        clear_done(out, f"rank_{month}")
    return StageResult(batch, int(files), int(rows), months)


def staged_months(out: Path) -> list[str]:
    root = out / "stage" / "soundings"
    return sorted(p.name.split("=", 1)[1] for p in root.glob("coll_month=*") if p.is_dir())


def _before_clause(before: datetime | None) -> str:
    return f"WHERE ingested < TIMESTAMP '{before:%Y-%m-%d %H:%M:%S}'" if before else ""


def rank(
    con: duckdb.DuckDBPyConnection,
    out: Path,
    month: str,
    *,
    before: datetime | None = None,
    dest: Path | None = None,
) -> None:
    """Pass 2: first-ingested copy wins; classify every later copy. One collection month at a time.

    ``before`` limits the census to files ingested before that instant and ``dest`` redirects the
    outputs, so the census "as of" a past date can be rebuilt from staged data (validation replay).
    """
    src = out / "stage" / "soundings" / f"coll_month={month}" / "*.parquet"
    root = (dest or out) / "rank"
    counts_dir, daily_dir, cells_dir = root / "file_counts", root / "daily", root / f"cells_r{CELL_RES}"
    vdays_dir = root / f"vdays_r{VDAY_RES}"
    for d in (counts_dir, daily_dir, cells_dir, vdays_dir):
        d.mkdir(parents=True, exist_ok=True)
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE ranked AS
        SELECT *,
               CASE WHEN row_number() OVER w = 1 THEN 'unique'
                    WHEN first_value(UNIQUE_ID) OVER w = UNIQUE_ID THEN 'dup_resend'
                    ELSE 'dup_cross_id' END AS class
        FROM (SELECT * FROM read_parquet('{_sql_path(src)}') {_before_clause(before)})
        WINDOW w AS (PARTITION BY key ORDER BY ingested, file
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        """
    )
    con.sql(
        f"""
        COPY (SELECT file, UNIQUE_ID AS unique_id, {TALLIES} FROM ranked GROUP BY ALL)
        TO '{_sql_path(counts_dir / f"{month}.parquet")}' (FORMAT parquet)
        """
    )
    con.sql(
        f"""
        COPY (
          SELECT PROVIDER AS provider,
                 CASE WHEN coll_month = 'invalid' THEN NULL ELSE left(TIME, 10) END AS coll_day,
                 {h3_cell(H3_RES)} AS h3_r{H3_RES},
                 {TALLIES},
                 count(DISTINCT UNIQUE_ID) AS platforms
          FROM ranked GROUP BY ALL
        ) TO '{_sql_path(daily_dir / f"{month}.parquet")}' (FORMAT parquet)
        """
    )
    # Map grain. No platform count: distinct counts are not additive across incremental runs.
    con.sql(
        f"""
        COPY (
          SELECT PROVIDER AS provider, coll_month, {h3_cell(CELL_RES)} AS h3_r{CELL_RES}, {TALLIES}
          FROM ranked GROUP BY ALL
        ) TO '{_sql_path(cells_dir / f"{month}.parquet")}' (FORMAT parquet)
        """
    )
    # Vessel-days: platforms are counted from these keys, which stay additive as tallies.
    con.sql(f"COPY ({vday_sql('ranked')}) TO '{_sql_path(vdays_dir / f'{month}.parquet')}' (FORMAT parquet)")
    con.sql("DROP TABLE ranked")


def finalize(
    con: duckdb.DuckDBPyConnection, out: Path, *, before: datetime | None = None, dest: Path | None = None
) -> Path:
    """Join per-file metadata with counts summed across collection months into ``file_index.parquet``."""
    root = dest or out
    files = _sql_path(out / "stage" / "files" / "*.parquet")
    counts = _sql_path(root / "rank" / "file_counts" / "*.parquet")
    index = root / "file_index.parquet"
    con.sql(
        f"""
        COPY (
          WITH c AS (
            -- sum() widens to HUGEINT, which Parquet stores as DOUBLE; keep counts integral.
            SELECT file, unique_id, sum(n_unique)::BIGINT AS n_unique,
                   sum(n_dup_resend)::BIGINT AS n_dup_resend, sum(n_dup_cross_id)::BIGINT AS n_dup_cross_id
            FROM read_parquet('{counts}') GROUP BY ALL
          )
          SELECT f.*, c.n_unique, c.n_dup_resend, c.n_dup_cross_id
          FROM (SELECT * FROM read_parquet('{files}') {_before_clause(before)}) f
          JOIN c USING (file, unique_id)
          ORDER BY f.ingested, f.file
        ) TO '{_sql_path(index)}' (FORMAT parquet)
        """
    )
    return index
