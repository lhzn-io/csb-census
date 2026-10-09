"""Keep the census current: seed from a backfill, then classify each new NCEI publication batch.

Counts stay additive. A new sounding is either a new unique or a duplicate of something already
counted; earlier counts never change except when NCEI removes a file (``reconcile``).

Per run (see docs/src/methodology.md, "Incremental updates"):

1. List the ingest days from ``max_ingested - lookback`` to now, plus pending keys. New objects
   are (key, ETag) pairs not yet in the file index; each run is capped so catch-up is gradual.
2. Whole-file resends: a new (file, UNIQUE_ID) group whose fingerprint and row count match a live
   indexed group is a duplicate in full, without re-reading anything.
3. Everything else is ranked against *candidate* files: live, same provider, holding originals,
   and overlapping in time and bounding box. Candidates are re-read from the source; earlier data
   always ranks first, so online the canonical copy is the first *published* one.
4. New rows are appended to every state table and the state is committed as a new generation.

Duplicates whose original came from a different provider are not seen online (4,873 rows in
the whole archive as of 2026-10-05); a garnet rebaseline recovers them.
"""

import csv
import logging
import shutil
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from csb_census.inventory import CSV_PREFIX, ObjectSource, S3Object, day_prefix
from csb_census.pipeline import CELL_RES, TALLIES, VDAY_RES, file_meta_sql, h3_cell, read_keyed, vday_sql
from csb_census.state import TABLES, State
from csb_census.underway import UW_COLUMNS, UW_RES, classify_files, keep_groups_sql, minutes_sql, underway_sql

log = logging.getLogger(__name__)

RECENT_DAYS = 30

COUNTS = "n_rows BIGINT, n_unique BIGINT, n_dup_resend BIGINT, n_dup_cross_id BIGINT"
DDL = {
    "file_index": f"""file VARCHAR, unique_id VARCHAR, provider VARCHAR, t_min VARCHAR, t_max VARCHAR,
        lon_min DOUBLE, lon_max DOUBLE, lat_min DOUBLE, lat_max DOUBLE, fingerprint UBIGINT,
        ingested TIMESTAMP, {COUNTS}, key VARCHAR, size BIGINT, etag VARCHAR, status VARCHAR,
        removed_at TIMESTAMP, method VARCHAR, dup_of VARCHAR, run_id VARCHAR, published_at TIMESTAMP""",
    "file_months": f"file VARCHAR, unique_id VARCHAR, coll_month VARCHAR, {COUNTS}",
    "cells_base": f"provider VARCHAR, coll_month VARCHAR, h3_r{CELL_RES} UBIGINT, {COUNTS}",
    "cells_delta": f"provider VARCHAR, coll_month VARCHAR, h3_r{CELL_RES} UBIGINT, {COUNTS}, run_id VARCHAR",
    "recent_cells": f"""file VARCHAR, unique_id VARCHAR, provider VARCHAR, ingested TIMESTAMP,
        coll_month VARCHAR, day VARCHAR, h3_r{CELL_RES} UBIGINT, {COUNTS}""",
    "vdays_base": f"provider VARCHAR, platform VARCHAR, day VARCHAR, h3_r{VDAY_RES} UBIGINT, {COUNTS}",
    "vdays_delta": f"""provider VARCHAR, platform VARCHAR, day VARCHAR, h3_r{VDAY_RES} UBIGINT, {COUNTS},
        run_id VARCHAR""",
    "uw_base": UW_COLUMNS,
    "uw_delta": f"{UW_COLUMNS}, file VARCHAR, run_id VARCHAR",
    "pending": "key VARCHAR, etag VARCHAR, seen_at TIMESTAMP",
    "queue": "provider VARCHAR, coll_month VARCHAR, reason VARCHAR, queued_at TIMESTAMP",
    "runs": f"""run_id VARCHAR, kind VARCHAR, run_at TIMESTAMP, generation BIGINT, new_files BIGINT, {COUNTS},
        reread_bytes BIGINT, capped BOOLEAN, removed BIGINT,
        newest_published TIMESTAMP, finished_at TIMESTAMP""",
}
assert set(DDL) == set(TABLES)


def _p(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def load_state(con: duckdb.DuckDBPyConnection, state: State) -> None:
    """Create every state table in ``con`` with its fixed schema, filled from the current generation."""
    for table, ddl in DDL.items():
        con.sql(f"CREATE OR REPLACE TEMP TABLE {table} ({ddl})")
        path = state.path(table)
        if path is not None:
            con.sql(f"INSERT INTO {table} BY NAME SELECT * FROM read_parquet('{_p(path)}')")


def save_tables(con: duckdb.DuckDBPyConnection, tables: Iterable[str], dest: Path) -> dict[str, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for table in tables:
        path = dest / f"{table}.parquet"
        con.sql(f"COPY {table} TO '{_p(path)}' (FORMAT parquet)")
        out[table] = path
    return out


def register_objects(
    con: duckdb.DuckDBPyConnection, table: str, objects: Iterable[S3Object], tmp: Path
) -> int:
    """Load a listing into ``table`` via a CSV (fast even for the full million-object listing)."""
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{table}.csv"
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "name", "size", "etag", "last_modified"])
        for o in objects:
            writer.writerow([o.key, o.name, o.size, o.etag, o.last_modified.astimezone(UTC).isoformat()])
            n += 1
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE {table} AS
        SELECT key, name, size::BIGINT AS size, etag,
               (last_modified::TIMESTAMPTZ AT TIME ZONE 'UTC') AS last_modified  -- naive UTC
        FROM read_csv('{_p(path)}', header = true, all_varchar = true)
        """
    )
    return n


def _log_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    kind: str,
    now: datetime,
    generation: int,
    new_files: int,
    tallies: Sequence[int],
    *,
    reread_bytes: int = 0,
    capped: bool = False,
    removed: int = 0,
    newest_published: datetime | None = None,
) -> None:
    """Append one row to ``runs`` (committed with the generation it describes).

    ``newest_published`` is the S3 publication time of the newest file the run took in; with
    ``finished_at`` it gives the census's latency behind NCEI.
    """
    con.execute(
        """INSERT INTO runs (run_id, kind, run_at, generation, new_files, n_rows, n_unique, n_dup_resend,
                             n_dup_cross_id, reread_bytes, capped, removed, newest_published, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            run_id,
            kind,
            now.astimezone(UTC).replace(tzinfo=None),
            generation,
            new_files,
            *(int(t) for t in tallies),
            reread_bytes,
            capped,
            removed,
            newest_published,
            datetime.now(UTC).replace(tzinfo=None),
        ],
    )


def _day_of_key(key: str) -> date | None:
    parts = key.split("/")
    try:
        return date(int(parts[2]), int(parts[3]), int(parts[4]))
    except (IndexError, ValueError):
        return None


@dataclass(frozen=True)
class AsOfSource:
    """Replay helper: a source that only lists objects published by ``as_of``."""

    inner: ObjectSource
    as_of: datetime

    def listing(self, prefix: str) -> Iterator[S3Object]:
        return (o for o in self.inner.listing(prefix) if o.last_modified <= self.as_of)

    def fetch(self, objects: Sequence[S3Object], dest: Path) -> list[Path]:
        return self.inner.fetch(objects, dest)


@dataclass(frozen=True)
class RunResult:
    run_id: str
    generation: int
    new_files: int
    new_rows: int
    n_unique: int
    n_dup_resend: int
    n_dup_cross_id: int
    fingerprint_groups: int
    reread_files: int
    reread_bytes: int
    capped: bool


def run(
    con: duckdb.DuckDBPyConnection,
    state: State,
    source: ObjectSource,
    *,
    now: datetime,
    run_id: str,
    max_files: int = 4000,
    max_bytes: int = 3_000_000_000,
    lookback_days: int = 3,
    work: Path | None = None,
) -> RunResult:
    """Classify everything NCEI published since the last run and commit a new state generation."""
    state.verify()
    load_state(con, state)
    work = Path(tempfile.mkdtemp(prefix="csb-census-")) if work is None else work
    try:
        return _run(con, state, source, now, run_id, max_files, max_bytes, lookback_days, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run(
    con: duckdb.DuckDBPyConnection,
    state: State,
    source: ObjectSource,
    now: datetime,
    run_id: str,
    max_files: int,
    max_bytes: int,
    lookback_days: int,
    work: Path,
) -> RunResult:
    m = state.manifest
    newest = datetime.fromisoformat(m.max_ingested).date() if m.max_ingested else now.date()
    days = {newest - timedelta(days=lookback_days) + timedelta(days=i) for i in range(lookback_days + 1)}
    days |= {newest + timedelta(days=i) for i in range((now.date() - newest).days + 1)}
    pending_keys = [k for (k,) in con.sql("SELECT key FROM pending").fetchall()]
    days |= {d for d in map(_day_of_key, pending_keys) if d is not None}
    listing = (o for d in sorted(days) for o in source.listing(day_prefix(d)))
    register_objects(con, "listed", listing, work)

    # New = (key, etag) not yet indexed; one row per key (latest listing wins); oldest first.
    candidates_new = con.sql(
        """
        SELECT l.key, l.name, l.size, l.etag, l.last_modified FROM listed l
        ANTI JOIN file_index i ON i.key = l.key AND i.etag = l.etag
        QUALIFY row_number() OVER (PARTITION BY l.key ORDER BY l.last_modified DESC) = 1
        ORDER BY l.name, l.key
        """
    ).fetchall()
    selected: list[S3Object] = []
    total = 0
    for key, _name, size, etag, modified in candidates_new:
        if selected and (len(selected) >= max_files or total + size > max_bytes):
            break
        selected.append(S3Object(key, int(size), modified.replace(tzinfo=UTC), etag))
        total += int(size)
    capped = len(selected) < len(candidates_new)
    if not selected:
        log.info("run %s: nothing new", run_id)
        return RunResult(run_id, m.generation, 0, 0, 0, 0, 0, 0, 0, 0, False)

    register_objects(con, "sel", selected, work)
    paths = source.fetch(selected, work / "new")
    read_keyed(con, paths, "new_rows")
    con.sql(f"CREATE OR REPLACE TEMP TABLE nf AS {file_meta_sql('new_rows')}")

    # Whole-file resends: same fingerprint and row count as a live group; earliest match wins.
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE fp AS
        SELECT n.file, n.unique_id, i.file AS dup_of, i.unique_id AS dup_uid
        FROM nf n JOIN file_index i
          ON i.status = 'live' AND i.fingerprint = n.fingerprint AND i.n_rows = n.n_rows
        QUALIFY row_number() OVER (PARTITION BY n.file, n.unique_id ORDER BY i.ingested, i.file) = 1
        """
    )
    con.sql("CREATE OR REPLACE TEMP TABLE um AS SELECT * FROM nf ANTI JOIN fp USING (file, unique_id)")
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE um_rows AS
        SELECT r.* FROM new_rows r SEMI JOIN um ON um.file = r.file AND um.unique_id = r.UNIQUE_ID
        """
    )

    # Candidate originals for everything else: same provider, live, holding uniques, overlapping.
    cand = con.sql(
        """
        SELECT DISTINCT i.key, i.size, i.etag, i.ingested FROM file_index i JOIN um
          ON i.status = 'live' AND i.provider = um.provider AND i.n_unique > 0
         AND i.t_min <= um.t_max AND i.t_max >= um.t_min
         AND i.lon_min <= um.lon_max AND i.lon_max >= um.lon_min
         AND i.lat_min <= um.lat_max AND i.lat_max >= um.lat_min
        WHERE i.key IS NOT NULL
        ORDER BY i.key
        """
    ).fetchall()
    cand_objs = [
        S3Object(k, int(s or 0), ing.replace(tzinfo=UTC) if ing else now, e or "") for k, s, e, ing in cand
    ]
    if cand_objs:
        read_keyed(con, source.fetch(cand_objs, work / "cand"), "cand_rows")
    else:
        con.sql("CREATE OR REPLACE TEMP TABLE cand_rows AS SELECT * FROM um_rows LIMIT 0")

    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE ranked AS
        SELECT *,
               CASE WHEN row_number() OVER w = 1 THEN 'unique'
                    WHEN first_value(UNIQUE_ID) OVER w = UNIQUE_ID THEN 'dup_resend'
                    ELSE 'dup_cross_id' END AS class
        FROM (
          SELECT c.*, 0 AS phase FROM cand_rows c SEMI JOIN um_rows u ON u.key = c.key
          UNION ALL BY NAME
          SELECT u.*, 1 AS phase FROM um_rows u
        )
        WINDOW w AS (PARTITION BY key ORDER BY phase, ingested, file
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        """
    )
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE cls AS
        SELECT * EXCLUDE (phase) FROM ranked WHERE phase = 1
        UNION ALL BY NAME
        SELECT r.*, CASE WHEN r.UNIQUE_ID = fp.dup_uid THEN 'dup_resend' ELSE 'dup_cross_id' END AS class
        FROM new_rows r JOIN fp ON fp.file = r.file AND fp.unique_id = r.UNIQUE_ID
        """
    )

    con.sql(
        f"""
        INSERT INTO file_index BY NAME
        SELECT n.*, c.n_unique, c.n_dup_resend, c.n_dup_cross_id,
               s.key, s.size, s.etag, 'live' AS status, NULL AS removed_at,
               CASE WHEN fp.dup_of IS NULL THEN 'ranked' ELSE 'fingerprint' END AS method,
               fp.dup_of, '{run_id}' AS run_id, s.last_modified AS published_at
        FROM nf n
        JOIN (SELECT file, UNIQUE_ID AS unique_id, {TALLIES} FROM cls GROUP BY ALL) c USING (file, unique_id)
        JOIN sel s ON s.name = n.file
        LEFT JOIN fp USING (file, unique_id)
        """
    )
    con.sql(
        f"""
        INSERT INTO file_months BY NAME
        SELECT file, UNIQUE_ID AS unique_id, coll_month, {TALLIES} FROM cls GROUP BY ALL
        """
    )
    con.sql(
        f"""
        INSERT INTO cells_delta BY NAME
        SELECT PROVIDER AS provider, coll_month, {h3_cell(CELL_RES)} AS h3_r{CELL_RES}, {TALLIES},
               '{run_id}' AS run_id
        FROM cls GROUP BY ALL
        """
    )
    con.sql(f"INSERT INTO vdays_delta BY NAME SELECT *, '{run_id}' AS run_id FROM ({vday_sql('cls')})")
    # Underway time: tracks of the new groups that hold unique soundings (resends add no time).
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE uw_keep AS
        SELECT file, UNIQUE_ID AS unique_id FROM cls GROUP BY ALL
        HAVING count(*) FILTER (WHERE class = 'unique') > 0
        """
    )
    con.sql(f"CREATE OR REPLACE TEMP TABLE uw_min AS {minutes_sql('new_rows', 'uw_keep')}")
    con.sql(
        f"INSERT INTO uw_delta BY NAME SELECT *, '{run_id}' AS run_id "
        f"FROM ({underway_sql('uw_min', with_file=True)})"
    )
    cutoff = now - timedelta(days=RECENT_DAYS)
    con.sql(f"DELETE FROM recent_cells WHERE ingested < TIMESTAMP '{cutoff:%Y-%m-%d %H:%M:%S}'")
    con.sql(
        f"""
        INSERT INTO recent_cells BY NAME
        SELECT file, UNIQUE_ID AS unique_id, PROVIDER AS provider, min(ingested) AS ingested, coll_month,
               CASE WHEN coll_month = 'invalid' THEN NULL ELSE left(TIME, 10) END AS day,
               {h3_cell(CELL_RES)} AS h3_r{CELL_RES}, {TALLIES}
        FROM cls GROUP BY file, UNIQUE_ID, PROVIDER, coll_month, day, h3_r{CELL_RES}
        """
    )
    con.sql("DELETE FROM pending WHERE key IN (SELECT key FROM sel)")

    tallies = con.sql(
        "SELECT count(*), count(*) FILTER (WHERE class = 'unique'), "
        "count(*) FILTER (WHERE class = 'dup_resend'), "
        "count(*) FILTER (WHERE class = 'dup_cross_id') FROM cls"
    ).fetchone() or (0, 0, 0, 0)
    max_ing = con.sql("SELECT max(ingested) FROM file_index").fetchone()
    newest_ingested = max_ing[0].isoformat() if max_ing and max_ing[0] else m.max_ingested
    reread_bytes = sum(o.size for o in cand_objs)
    nfp = len(con.sql("SELECT 1 FROM fp").fetchall())
    _log_run(
        con,
        run_id,
        "incremental",
        now,
        m.generation + 1,
        len(selected),
        tallies,
        reread_bytes=reread_bytes,
        capped=capped,
        newest_published=max(o.last_modified for o in selected).astimezone(UTC).replace(tzinfo=None),
    )

    changed = (
        "file_index",
        "file_months",
        "cells_delta",
        "recent_cells",
        "vdays_delta",
        "uw_delta",
        "pending",
        "runs",
    )
    manifest = state.commit(
        save_tables(con, changed, work / "out"),
        max_ingested=newest_ingested,
        last_listed_day=now.date().isoformat(),
    )
    result = RunResult(
        run_id,
        manifest.generation,
        len(selected),
        int(tallies[0]),
        int(tallies[1]),
        int(tallies[2]),
        int(tallies[3]),
        nfp,
        len(cand_objs),
        reread_bytes,
        capped,
    )
    log.info("run %s: %s", run_id, result)
    return result


@dataclass(frozen=True)
class ReconcileResult:
    generation: int
    listed: int
    removed: int
    republished: int
    newly_pending: int
    queued_months: int


def reconcile(
    con: duckdb.DuckDBPyConnection, state: State, source: ObjectSource, *, now: datetime, work: Path
) -> ReconcileResult:
    """Compare the full listing with the index: mark removals, queue republished and missed keys."""
    state.verify()
    load_state(con, state)
    listed = register_objects(con, "listed", source.listing(CSV_PREFIX), work)
    stamp = f"TIMESTAMP '{now:%Y-%m-%d %H:%M:%S}'"
    con.sql(
        """
        CREATE OR REPLACE TEMP TABLE gone AS
        SELECT i.file, i.unique_id, i.key, i.provider, i.n_unique,
               EXISTS (SELECT 1 FROM listed l WHERE l.key = i.key) AS republished
        FROM file_index i
        WHERE i.status = 'live'
          AND NOT EXISTS (SELECT 1 FROM listed l WHERE l.key = i.key AND l.etag = i.etag)
        """
    )
    counts = con.sql("SELECT count(*), count(*) FILTER (WHERE republished) FROM gone").fetchone()
    removed, republished = counts if counts else (0, 0)
    con.sql(
        f"""
        UPDATE file_index SET status = 'removed', removed_at = {stamp}
        WHERE status = 'live'
          AND EXISTS (SELECT 1 FROM gone g
                      WHERE g.file = file_index.file AND g.unique_id = file_index.unique_id)
        """
    )
    # Map cells: subtract exactly where per-file cells are still held; otherwise queue a recount.
    con.sql(
        f"""
        INSERT INTO cells_delta BY NAME
        SELECT provider, coll_month, h3_r{CELL_RES}, -sum(n_rows) AS n_rows, -sum(n_unique) AS n_unique,
               -sum(n_dup_resend) AS n_dup_resend, -sum(n_dup_cross_id) AS n_dup_cross_id,
               'reconcile-{now:%Y%m%dT%H%M}' AS run_id
        FROM recent_cells r SEMI JOIN gone g ON g.file = r.file AND g.unique_id = r.unique_id
        GROUP BY ALL
        """
    )
    con.sql(
        f"""
        INSERT INTO vdays_delta BY NAME
        SELECT provider, unique_id AS platform, day,
               h3_cell_to_parent(h3_r{CELL_RES}, {VDAY_RES}) AS h3_r{VDAY_RES},
               -sum(n_rows) AS n_rows, -sum(n_unique) AS n_unique,
               -sum(n_dup_resend) AS n_dup_resend, -sum(n_dup_cross_id) AS n_dup_cross_id,
               'reconcile-{now:%Y%m%dT%H%M}' AS run_id
        FROM recent_cells r SEMI JOIN gone g ON g.file = r.file AND g.unique_id = r.unique_id
        WHERE day IS NOT NULL AND h3_r{CELL_RES} IS NOT NULL
        GROUP BY ALL
        """
    )
    # Underway time is held per file in uw_delta, so a removed file published since the seed comes out
    # exactly. Groups in uw_base hold unique soundings, so the queue below already covers their months.
    con.sql(
        f"""
        INSERT INTO uw_delta BY NAME
        SELECT file, provider, platform, day, h3_r{UW_RES},
               -sum(min_underway) AS min_underway, -sum(min_slow) AS min_slow,
               -sum(min_stationary) AS min_stationary, -sum(min_other) AS min_other,
               -sum(n_soundings) AS n_soundings, 'reconcile-{now:%Y%m%dT%H%M}' AS run_id
        FROM uw_delta u SEMI JOIN gone g ON g.file = u.file AND g.unique_id = u.platform
        GROUP BY ALL
        """
    )
    con.sql(
        f"""
        INSERT INTO queue BY NAME
        SELECT DISTINCT g.provider, m.coll_month,
               CASE WHEN g.n_unique > 0 THEN 'removed original' ELSE 'removed, cells not held' END AS reason,
               {stamp} AS queued_at
        FROM gone g JOIN file_months m ON m.file = g.file AND m.unique_id = g.unique_id
        WHERE g.n_unique > 0
           OR NOT EXISTS (SELECT 1 FROM recent_cells r WHERE r.file = g.file AND r.unique_id = g.unique_id)
        """
    )
    con.sql(
        """
        DELETE FROM recent_cells
        WHERE EXISTS (SELECT 1 FROM gone g
                      WHERE g.file = recent_cells.file AND g.unique_id = recent_cells.unique_id)
        """
    )
    before = len(con.sql("SELECT 1 FROM pending").fetchall())
    con.sql(
        f"""
        INSERT INTO pending
        SELECT l.key, l.etag, {stamp} FROM listed l
        WHERE NOT EXISTS (SELECT 1 FROM file_index i WHERE i.key = l.key AND i.etag = l.etag)
          AND NOT EXISTS (SELECT 1 FROM pending p WHERE p.key = l.key AND p.etag = l.etag)
        """
    )
    newly_pending = len(con.sql("SELECT 1 FROM pending").fetchall()) - before
    queued = len(con.sql("SELECT DISTINCT provider, coll_month FROM queue").fetchall())
    _log_run(
        con,
        f"reconcile-{now:%Y%m%dT%H%M}",
        "reconcile",
        now,
        state.manifest.generation + 1,
        0,
        (0, 0, 0, 0),
        removed=int(removed),
    )
    changed = (
        "file_index",
        "cells_delta",
        "recent_cells",
        "vdays_delta",
        "uw_delta",
        "pending",
        "queue",
        "runs",
    )
    manifest = state.commit(save_tables(con, changed, work / "out"))
    result = ReconcileResult(
        manifest.generation, listed, int(removed), int(republished), newly_pending, queued
    )
    log.info("reconcile: %s", result)
    return result


def seed(
    con: duckdb.DuckDBPyConnection, backfill: Path, state: State, source: ObjectSource, *, work: Path
) -> int:
    """Build generation 1 from a finished backfill (``file_index.parquet`` and ``rank/``).

    Joins a full listing to recover each file's key and ETag. Indexed files that no longer exist
    are marked removed; listed files the backfill never saw become pending.
    """
    if state.manifest.generation:
        raise ValueError(
            f"state already at generation {state.manifest.generation}; seed needs an empty state"
        )
    for table, ddl in DDL.items():
        con.sql(f"CREATE OR REPLACE TEMP TABLE {table} ({ddl})")
    register_objects(con, "listed", source.listing(CSV_PREFIX), work)
    idx, rank = backfill / "file_index.parquet", backfill / "rank"
    con.sql(
        f"""
        INSERT INTO file_index BY NAME
        SELECT b.*, l.key, l.size, l.etag, l.last_modified AS published_at,
               CASE WHEN l.key IS NULL THEN 'removed' ELSE 'live' END AS status,
               NULL AS removed_at, 'backfill' AS method, NULL AS dup_of, 'seed' AS run_id
        FROM read_parquet('{_p(idx)}') b LEFT JOIN listed l ON l.name = b.file
        """
    )
    con.sql(
        f"""
        INSERT INTO file_months BY NAME
        SELECT * EXCLUDE (filename), regexp_extract(filename, '([0-9]{{4}}-[0-9]{{2}}|invalid)\\.parquet$', 1)
               AS coll_month
        FROM read_parquet('{_p(rank / "file_counts" / "*.parquet")}', filename = true)
        """
    )
    con.sql(
        f"""
        INSERT INTO cells_base BY NAME
        SELECT provider, coll_month, h3_r{CELL_RES}, sum(n_rows) AS n_rows, sum(n_unique) AS n_unique,
               sum(n_dup_resend) AS n_dup_resend, sum(n_dup_cross_id) AS n_dup_cross_id
        FROM read_parquet('{_p(rank / f"cells_r{CELL_RES}" / "*.parquet")}') GROUP BY ALL
        """
    )
    vdays = rank / f"vdays_r{VDAY_RES}"
    if not any(vdays.glob("*.parquet")):
        raise FileNotFoundError(f"{vdays} is empty; re-run `csb-census rank` to add vessel-days")
    con.sql(f"INSERT INTO vdays_base BY NAME SELECT * FROM read_parquet('{_p(vdays / '*.parquet')}')")
    max_ing = con.sql("SELECT max(ingested) FROM file_index").fetchone()
    newest = max_ing[0] if max_ing and max_ing[0] else None
    if newest is not None:
        # Listed but never staged, at or before the backfill's horizon: missed, so pending.
        con.sql(
            f"""
            INSERT INTO pending
            SELECT l.key, l.etag, now() FROM listed l
            WHERE NOT EXISTS (SELECT 1 FROM file_index i WHERE i.key = l.key)
              AND try_strptime(left(l.name, 14), '%Y%m%d%H%M%S') <= TIMESTAMP '{newest:%Y-%m-%d %H:%M:%S}'
            """
        )
    manifest = state.commit(
        save_tables(con, TABLES, work / "out"),
        max_ingested=newest.isoformat() if newest else None,
    )
    return manifest.generation


@dataclass(frozen=True)
class UnderwaySeed:
    generation: int
    cached_chunks: int
    classified_files: int
    rows: int
    underway_minutes: int
    stationary_minutes: int


def seed_underway(
    con: duckdb.DuckDBPyConnection,
    state: State,
    minutes: Path,
    source: ObjectSource,
    *,
    work: Path,
    chunk_files: int = 15_000,
) -> UnderwaySeed:
    """Rebuild ``uw_base`` for every live group with unique soundings, and empty ``uw_delta``.

    ``minutes`` is a cache of minute rows (``*.parquet``, as written by ``underway.classify_files``).
    Files the cache does not cover are downloaded and classified into new chunks there, so an empty
    cache classifies the whole archive and a re-run resumes. Rows of files no longer live are ignored.
    """
    load_state(con, state)
    minutes.mkdir(parents=True, exist_ok=True)
    con.sql(f"CREATE OR REPLACE TEMP TABLE uw_keep AS {keep_groups_sql('file_index')}")
    cached = sorted(minutes.glob("*.parquet"))
    covered = "(SELECT NULL::VARCHAR AS file WHERE false)"
    if cached:
        covered = f"(SELECT DISTINCT file FROM read_parquet('{_p(minutes / '*.parquet')}'))"
    missing = con.sql(
        f"""
        SELECT DISTINCT i.key, i.size, coalesce(i.published_at, i.ingested) AS t
        FROM file_index i SEMI JOIN uw_keep k ON k.file = i.file AND k.unique_id = i.unique_id
        WHERE i.key IS NOT NULL AND i.file NOT IN {covered}
        ORDER BY i.key
        """
    ).fetchall()
    log.info("underway seed: %d cached chunks, %d files to classify", len(cached), len(missing))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for n, start in enumerate(range(0, len(missing), chunk_files)):
        chunk = missing[start : start + chunk_files]
        objects = [S3Object(k, int(s or 0), t.replace(tzinfo=UTC)) for k, s, t in chunk]
        tmp = work / "uw-csv"
        paths = source.fetch(objects, tmp)
        classify_files(con, paths, "uw_keep", minutes / f"gap_{stamp}_{n:05d}.parquet")
        shutil.rmtree(tmp, ignore_errors=True)
        log.info(
            "underway seed: classified %d of %d files", min(start + chunk_files, len(missing)), len(missing)
        )

    con.sql("DELETE FROM uw_base")
    con.sql("DELETE FROM uw_delta")
    if any(minutes.glob("*.parquet")):
        con.sql(
            f"""
            CREATE OR REPLACE TEMP TABLE uw_min AS
            SELECT m.* FROM read_parquet('{_p(minutes / "*.parquet")}') m
            SEMI JOIN uw_keep k ON k.file = m.file AND k.unique_id = m.platform
            """
        )
        con.sql(f"INSERT INTO uw_base BY NAME {underway_sql('uw_min')}")
    totals = con.sql("SELECT count(*), sum(min_underway), sum(min_stationary) FROM uw_base").fetchone()
    assert totals is not None
    manifest = state.commit(save_tables(con, ("uw_base", "uw_delta"), work / "out"))
    result = UnderwaySeed(
        manifest.generation,
        len(cached),
        len(missing),
        int(totals[0]),
        int(totals[1] or 0),
        int(totals[2] or 0),
    )
    log.info("underway seed: %s", result)
    return result
