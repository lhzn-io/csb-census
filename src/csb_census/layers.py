"""Published data for the dashboard, built from the census state.

Layout under ``out`` (served by GitHub Pages, so browsers can fetch it cross-origin)::

    meta.json                          totals, generation, last ingest, build time
    timeseries_month.json              counts per collection month (community; per provider if enabled)
    recent.json                        recent activity: windows by publication, batches, daily series, lag
    layers/manifest.json               every layer file with its bounding box, for viewport loading
    layers/r4/cells.parquet            global overview (map zoom 0-4)
    layers/r6/part=<r1>/cells.parquet  regional detail, one file per H3 r1 parent (zoom 5-7)
    layers/r8/part=<r2>/cells.parquet  local detail, one file per H3 r2 parent (zoom 8+)
    layers/recent/<window>/r<res>/...  vessel-days collected in the last 7 d, 30 d or 365 d
    layers/providers/<slug>/...        the same per provider label, only with ``providers=True``
    spilhaus/<center>/cells.json       the r4 layer projected into the world-ocean square (landing page)

Archive layer files have the columns ``h3 (VARCHAR), n_unique, n_published, dup_share, n_providers,
first_year, last_year, vessel_days, platforms``. Window files have ``h3, vessel_days, platforms,
n_unique, n_published``. A sounding and all its duplicates share one position, so they always fall
in the same cell.

Two clocks are used, and every output says which: *publication* is the NCEI file stamp
(``file_index.ingested``), *collection* is the sounding's own TIME.
"""

import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from csb_census import spilhaus
from csb_census.pipeline import CELL_RES, VDAY_RES
from csb_census.state import State

LEVELS = {4: None, 6: 1, 8: 2}  # resolution -> parent resolution used to split files
PAD_DEG = {4: 0.3, 6: 0.05, 8: 0.01}  # about one cell edge, so tile bboxes cover whole hexagons
WINDOWS = {"7d": (7, (4, 6, 8)), "30d": (30, (4, 6, 8)), "365d": (365, (4, 6))}  # collection-day windows
STRIP = {"24h": 1, "7d": 7, "30d": 30, "all": None}  # publication windows, in days
LAG_BUCKETS = [("<6h", 6), ("6-24h", 24), ("1-3d", 72), ("3-7d", 168), ("7-30d", 720), ("30-365d", 8760)]
LAG_LAST = ">1y"


def _p(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _ts(t: datetime) -> str:
    return f"TIMESTAMP '{t.astimezone(UTC):%Y-%m-%d %H:%M:%S}'"


def slug(provider: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-") or "unknown"


def _base_plus_delta(state: State, base: str, delta: str) -> str | None:
    """SQL over a base table plus its signed deltas (``run_id`` dropped); None when the base is absent."""
    first = state.path(base)
    if first is None:
        return None
    src = f"SELECT * FROM '{_p(first)}'"
    extra = state.path(delta)
    return f"{src} UNION ALL BY NAME SELECT * EXCLUDE (run_id) FROM '{_p(extra)}'" if extra else src


def load_cells(con: duckdb.DuckDBPyConnection, state: State) -> None:
    """All-time cell counts per (provider, collection month, r9): rebaseline plus every delta."""
    state.require("cells_base")
    src = _base_plus_delta(state, "cells_base", "cells_delta")
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE cells AS
        SELECT provider, coll_month, h3_r{CELL_RES} AS h3,
               sum(n_rows)::BIGINT AS n_rows, sum(n_unique)::BIGINT AS n_unique
        FROM ({src})
        WHERE h3_r{CELL_RES} IS NOT NULL AND coll_month <> 'invalid'
        GROUP BY ALL HAVING sum(n_rows) > 0
        """
    )


def load_vdays(con: duckdb.DuckDBPyConnection, state: State) -> None:
    """Vessel-days per (provider, platform, collection day, r8), with their sounding counts.

    A key counts when it holds soundings other than cross-ID duplicates, so data repeated under
    another platform ID never adds a vessel. Empty for a state seeded before vessel-days existed.
    """
    src = _base_plus_delta(state, "vdays_base", "vdays_delta")
    if src is None:
        con.sql(
            "CREATE OR REPLACE TEMP TABLE vd (provider VARCHAR, platform VARCHAR, day VARCHAR, h3 UBIGINT, "
            "n_rows BIGINT, n_unique BIGINT)"
        )
        return
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE vd AS
        SELECT provider, platform, day, h3_r{VDAY_RES} AS h3,
               sum(n_rows)::BIGINT AS n_rows, sum(n_unique)::BIGINT AS n_unique
        FROM ({src})
        GROUP BY provider, platform, day, h3_r{VDAY_RES}
        HAVING sum(n_rows - n_dup_cross_id) > 0
        """
    )


def _vessel_sql(res: int, where: str) -> str:
    """Vessel-days and platforms per cell at ``res``, from ``vd``."""
    return f"""
        SELECT h3_cell_to_parent(h3, {res}) AS cell,
               count(DISTINCT platform || '|' || day)::INTEGER AS vessel_days,
               count(DISTINCT platform)::INTEGER AS platforms,
               sum(n_unique)::BIGINT AS n_unique, sum(n_rows)::BIGINT AS n_published
        FROM vd {where} GROUP BY 1
    """


def _archive_sql(res: int, where: str) -> str:
    return f"""
        SELECT c.*, coalesce(v.vessel_days, 0)::INTEGER AS vessel_days,
               coalesce(v.platforms, 0)::INTEGER AS platforms
        FROM (
          SELECT h3_cell_to_parent(h3, {res}) AS cell,
                 sum(n_unique)::BIGINT AS n_unique, sum(n_rows)::BIGINT AS n_published,
                 round(1 - sum(n_unique) / sum(n_rows), 4) AS dup_share,
                 count(DISTINCT provider)::INTEGER AS n_providers,
                 min(left(coll_month, 4))::INTEGER AS first_year,
                 max(left(coll_month, 4))::INTEGER AS last_year
          FROM cells {where} GROUP BY 1
        ) c LEFT JOIN ({_vessel_sql(res, where)}) v USING (cell)
    """


def _write_level(
    con: duckdb.DuckDBPyConnection, cells_sql: str, res: int, dest: Path, base_url: str
) -> list[dict[str, Any]]:
    """Write one resolution from ``cells_sql`` (a ``cell UBIGINT`` column plus values), split by parent."""
    parent_res = LEVELS[res]
    part = f"h3_h3_to_string(h3_cell_to_parent(cell, {parent_res}))" if parent_res is not None else "'all'"
    pad = PAD_DEG[res]
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE lvl AS
        SELECT h3_h3_to_string(cell) AS h3, {part} AS part, * EXCLUDE (cell)
        FROM ({cells_sql}) ORDER BY part, h3
        """
    )
    dest.mkdir(parents=True, exist_ok=True)
    tiles: list[dict[str, Any]] = []
    if not con.sql("SELECT 1 FROM lvl LIMIT 1").fetchall():
        return tiles
    con.sql(
        f"""
        COPY (SELECT * FROM lvl) TO '{_p(dest)}'
        (FORMAT parquet, COMPRESSION snappy, PARTITION_BY (part), OVERWRITE_OR_IGNORE,
         FILENAME_PATTERN 'cells_{{i}}')
        """
    )
    for part_id, rows, w, s, e, n in con.sql(
        """
        SELECT part, count(*),
               min(h3_cell_to_lng(h3_string_to_h3(h3))), min(h3_cell_to_lat(h3_string_to_h3(h3))),
               max(h3_cell_to_lng(h3_string_to_h3(h3))), max(h3_cell_to_lat(h3_string_to_h3(h3)))
        FROM lvl GROUP BY part ORDER BY part
        """
    ).fetchall():
        files = sorted((dest / f"part={part_id}").glob("*.parquet"))
        if e - w > 180:  # spans the antimeridian: let it match every viewport longitude
            w, e = -180.0, 180.0
        tiles.append(
            {
                "res": res,
                "part": part_id,
                "rows": int(rows),
                "bbox": [
                    round(max(-180.0, w - pad), 4),
                    round(max(-90.0, s - pad), 4),
                    round(min(180.0, e + pad), 4),
                    round(min(90.0, n + pad), 4),
                ],
                "files": [f"{base_url}/r{res}/part={part_id}/{f.name}" for f in files],
                "bytes": sum(f.stat().st_size for f in files),
            }
        )
    return tiles


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[list[Any]]:
    out = []
    for row in con.sql(sql).fetchall():
        out.append([v.isoformat(timespec="minutes") if isinstance(v, datetime) else v for v in row])
    return out


def _lag_case() -> str:
    cases = " ".join(f"WHEN lag_h < {hours} THEN '{name}'" for name, hours in LAG_BUCKETS)
    return f"CASE {cases} ELSE '{LAG_LAST}' END"


def _recent(con: duckdb.DuckDBPyConnection, state: State, now: datetime, providers: bool) -> dict[str, Any]:
    """Recent activity by publication (file stamp) and by collection day."""
    idx = _p(state.require("file_index"))
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE pub AS
        SELECT file, unique_id, provider, ingested, n_rows, n_unique,
               greatest(0, date_diff('second', try_strptime(left(t_max, 19), '%Y-%m-%dT%H:%M:%S'), ingested)
                        / 3600.0) AS lag_h,
               min(ingested) OVER (PARTITION BY unique_id) AS first_seen
        FROM '{idx}' WHERE status = 'live'
        """
    )

    def strip(where: str) -> dict[str, dict[str, Any]]:
        out = {}
        for name, days in STRIP.items():
            since = f"ingested >= {_ts(now - timedelta(days=days))}" if days else "TRUE"
            row = con.sql(
                f"""
                SELECT count(DISTINCT file), coalesce(sum(n_rows), 0)::BIGINT,
                       coalesce(sum(n_unique), 0)::BIGINT, count(DISTINCT unique_id),
                       count(DISTINCT unique_id) FILTER (WHERE first_seen = ingested),
                       count(DISTINCT provider), round(median(lag_h), 1)
                FROM pub WHERE {since} {where}
                """
            ).fetchone()
            assert row is not None
            keys = ("files", "published", "unique", "platforms", "new_platforms", "providers", "median_lag_h")
            out[name] = dict(zip(keys, row, strict=True))
        return out

    def lag_hist(since: str) -> list[list[Any]]:
        counts = dict(con.sql(f"SELECT {_lag_case()}, count(*) FROM pub WHERE {since} GROUP BY 1").fetchall())
        return [[name, int(counts.get(name, 0))] for name, _ in [*LAG_BUCKETS, (LAG_LAST, 0)]]

    d30, d400 = _ts(now - timedelta(days=30)), _ts(now - timedelta(days=400))
    day400 = (now - timedelta(days=400)).date().isoformat()
    recent: dict[str, Any] = {
        "now": now.astimezone(UTC).isoformat(timespec="minutes"),
        "strip": strip(""),
        "batches": {
            "columns": ["batch", "files", "published", "unique"],
            "rows": _rows(
                con,
                f"""SELECT time_bucket(INTERVAL 6 HOUR, ingested) AS b, count(DISTINCT file),
                           sum(n_rows)::BIGINT, sum(n_unique)::BIGINT
                    FROM pub WHERE ingested >= {d30} GROUP BY 1 ORDER BY 1""",
            ),
        },
        "daily_pub": {
            "columns": ["day", "files", "published", "unique", "platforms"],
            "rows": _rows(
                con,
                f"""SELECT strftime(ingested, '%Y-%m-%d'), count(DISTINCT file), sum(n_rows)::BIGINT,
                           sum(n_unique)::BIGINT, count(DISTINCT unique_id)
                    FROM pub WHERE ingested >= {d400} GROUP BY 1 ORDER BY 1""",
            ),
        },
        "daily_coll": {
            "columns": ["day", "published", "unique", "platforms", "vessel_cells"],
            "rows": _rows(
                con,
                f"""SELECT day, sum(n_rows)::BIGINT, sum(n_unique)::BIGINT, count(DISTINCT platform), count(*)
                    FROM vd WHERE day >= '{day400}' AND day <= '{now.date().isoformat()}'
                    GROUP BY 1 ORDER BY 1""",
            ),
        },
        "lag_hist": {
            "buckets": [n for n, _ in LAG_BUCKETS] + [LAG_LAST],
            "last_30d": lag_hist(f"ingested >= {d30}"),
            "all": lag_hist("TRUE"),
        },
        "runs": {"columns": [], "rows": []},
    }
    runs = state.path("runs")
    if runs is not None:
        cols = [
            "run_id",
            "kind",
            "run_at",
            "generation",
            "new_files",
            "n_rows",
            "n_unique",
            "n_dup_resend",
            "n_dup_cross_id",
            "reread_bytes",
            "capped",
            "removed",
        ]
        recent["runs"] = {
            "columns": cols,
            "rows": _rows(con, f"SELECT {', '.join(cols)} FROM '{_p(runs)}' ORDER BY run_at DESC LIMIT 120"),
        }
    if providers:
        names = [n for (n,) in con.sql("SELECT DISTINCT provider FROM pub ORDER BY 1").fetchall()]
        recent["providers"] = {n: strip(f"AND provider = '{n.replace(chr(39), chr(39) * 2)}'") for n in names}
    return recent


def build(
    con: duckdb.DuckDBPyConnection,
    state: State,
    out: Path,
    *,
    providers: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write every published file under ``out``; returns ``meta``. ``now`` anchors the recent windows."""
    state.verify()
    now = now or datetime.now(UTC)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    load_cells(con, state)
    load_vdays(con, state)
    idx, months = _p(state.require("file_index")), _p(state.require("file_months"))

    manifest: dict[str, Any] = {
        "levels": {str(r): {"parent": p} for r, p in LEVELS.items()},
        "tiles": [],
        "windows": {},
    }
    for res in LEVELS:
        manifest["tiles"] += _write_level(
            con, _archive_sql(res, ""), res, out / "layers" / f"r{res}", "layers"
        )
    spilhaus.write_cells(con, _p(out / "layers" / "r4" / "*" / "*.parquet"), out)
    today = now.date()
    for name, (days, levels) in WINDOWS.items():
        base = f"layers/recent/{name}"
        where = f"WHERE day > '{(today - timedelta(days=days)).isoformat()}' AND day <= '{today.isoformat()}'"
        manifest["windows"][name] = [
            t
            for res in levels
            for t in _write_level(con, _vessel_sql(res, where), res, out / base / f"r{res}", base)
        ]
    if providers:
        manifest["providers"] = {}
        for (name,) in con.sql("SELECT DISTINCT provider FROM cells ORDER BY 1").fetchall():
            base = f"layers/providers/{slug(name)}"
            where = f"WHERE provider = '{name.replace("'", "''")}'"
            manifest["providers"][name] = [
                t
                for res in (4, 6)
                for t in _write_level(con, _archive_sql(res, where), res, out / base / f"r{res}", base)
            ]
    (out / "layers" / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))

    totals = con.sql(
        f"""
        SELECT sum(n_rows)::BIGINT, sum(n_unique)::BIGINT,
               sum(n_dup_resend)::BIGINT, sum(n_dup_cross_id)::BIGINT,
               count(DISTINCT file), count(DISTINCT provider), count(DISTINCT unique_id), max(ingested)
        FROM '{idx}' WHERE status = 'live'
        """
    ).fetchone()
    assert totals is not None
    mapped = con.sql("SELECT sum(n_rows)::BIGINT, sum(n_unique)::BIGINT FROM cells").fetchone() or (0, 0)
    since30 = (today - timedelta(days=30)).isoformat()
    vessels = con.sql(
        f"""SELECT count(DISTINCT platform || '|' || day),
                   count(DISTINCT platform) FILTER (WHERE day > '{since30}' AND day <= '{today.isoformat()}')
            FROM vd"""
    ).fetchone() or (0, 0)
    meta = {
        "generation": state.manifest.generation,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "last_ingested": totals[7].isoformat() if totals[7] else None,
        "published": totals[0],
        "unique": totals[1],
        "dup_resend": totals[2],
        "dup_cross_id": totals[3],
        "files": totals[4],
        "providers": totals[5],
        "platforms": totals[6],
        "mapped_published": mapped[0],
        "mapped_unique": mapped[1],
        "vessel_days": vessels[0],
        "platforms_active_30d": vessels[1],
        "provider_views": providers,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    series_sql = f"""
        SELECT m.coll_month, {{group}} sum(m.n_rows)::BIGINT, sum(m.n_unique)::BIGINT,
               sum(m.n_dup_resend)::BIGINT, sum(m.n_dup_cross_id)::BIGINT
        FROM '{months}' m JOIN '{idx}' i USING (file, unique_id)
        WHERE i.status = 'live' AND m.coll_month <> 'invalid'
        GROUP BY ALL ORDER BY ALL
    """
    cols = ["month", "published", "unique", "dup_resend", "dup_cross_id"]
    series: dict[str, Any] = {
        "columns": cols,
        "community": [list(r) for r in con.sql(series_sql.format(group="")).fetchall()],
    }
    if providers:
        by: dict[str, list[list[Any]]] = {}
        for row in con.sql(series_sql.format(group="i.provider,")).fetchall():
            by.setdefault(row[1], []).append([row[0], *row[2:]])
        series["providers"] = by
    (out / "timeseries_month.json").write_text(json.dumps(series, separators=(",", ":")))
    (out / "recent.json").write_text(json.dumps(_recent(con, state, now, providers), separators=(",", ":")))
    return meta
