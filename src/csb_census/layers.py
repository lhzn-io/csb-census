"""Published data for the dashboard, built from the census state.

Layout under ``out`` (served by GitHub Pages, so browsers can fetch it cross-origin)::

    meta.json                          totals, generation, last ingest, build time
    timeseries_month.json              counts per collection month (community; per provider if enabled)
    layers/manifest.json               every layer file with its bounding box, for viewport loading
    layers/r4/cells.parquet            global overview (map zoom 0-4)
    layers/r6/part=<r1>/cells.parquet  regional detail, one file per H3 r1 parent (zoom 5-7)
    layers/r8/part=<r2>/cells.parquet  local detail, one file per H3 r2 parent (zoom 8+)
    layers/providers/<slug>/...        the same per provider label, only with ``providers=True``

Every layer file has the columns
``h3 (VARCHAR), n_unique, n_published, dup_share, n_providers, first_year, last_year``.
A sounding and all its duplicates share one position, so they always fall in the same cell.
"""

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from csb_census.pipeline import CELL_RES
from csb_census.state import State

LEVELS = {4: None, 6: 1, 8: 2}  # resolution -> parent resolution used to split files
PAD_DEG = {4: 0.3, 6: 0.05, 8: 0.01}  # about one cell edge, so tile bboxes cover whole hexagons


def _p(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def slug(provider: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", provider.lower()).strip("-") or "unknown"


def load_cells(con: duckdb.DuckDBPyConnection, state: State) -> None:
    """All-time cell counts per (provider, collection month, r9): rebaseline plus every delta."""
    parts = [state.require("cells_base")]
    delta = state.path("cells_delta")
    if delta is not None:
        parts.append(delta)
    union = " UNION ALL BY NAME ".join(f"SELECT * EXCLUDE (run_id) FROM '{_p(p)}'" for p in parts[1:])
    base = f"SELECT * FROM '{_p(parts[0])}'"
    src = f"{base} UNION ALL BY NAME {union}" if union else base
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


def _write_level(
    con: duckdb.DuckDBPyConnection, where: str, res: int, dest: Path, base_url: str
) -> list[dict[str, Any]]:
    parent_res = LEVELS[res]
    part = f"h3_h3_to_string(h3_cell_to_parent(h3, {parent_res}))" if parent_res is not None else "'all'"
    pad = PAD_DEG[res]
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE lvl AS
        SELECT h3_h3_to_string(h3_cell_to_parent(h3, {res})) AS h3, {part} AS part,
               sum(n_unique)::BIGINT AS n_unique, sum(n_rows)::BIGINT AS n_published,
               round(1 - sum(n_unique) / sum(n_rows), 4) AS dup_share,
               count(DISTINCT provider)::INTEGER AS n_providers,
               min(left(coll_month, 4))::INTEGER AS first_year, max(left(coll_month, 4))::INTEGER AS last_year
        FROM cells {where} GROUP BY 1, 2 ORDER BY part, h3
        """
    )
    dest.mkdir(parents=True, exist_ok=True)
    con.sql(
        f"""
        COPY (SELECT * FROM lvl) TO '{_p(dest)}'
        (FORMAT parquet, COMPRESSION snappy, PARTITION_BY (part), OVERWRITE_OR_IGNORE,
         FILENAME_PATTERN 'cells_{{i}}')
        """
    )
    tiles = []
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


def build(
    con: duckdb.DuckDBPyConnection, state: State, out: Path, *, providers: bool = False
) -> dict[str, Any]:
    """Write every published file under ``out``; returns ``meta``."""
    state.verify()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    load_cells(con, state)
    idx, months = _p(state.require("file_index")), _p(state.require("file_months"))

    manifest: dict[str, Any] = {"levels": {str(r): {"parent": p} for r, p in LEVELS.items()}, "tiles": []}
    for res in LEVELS:
        manifest["tiles"] += _write_level(con, "", res, out / "layers" / f"r{res}", "layers")
    if providers:
        manifest["providers"] = {}
        for (name,) in con.sql("SELECT DISTINCT provider FROM cells ORDER BY 1").fetchall():
            base = f"layers/providers/{slug(name)}"
            where = f"WHERE provider = '{name.replace("'", "''")}'"
            manifest["providers"][name] = [
                t for res in (4, 6) for t in _write_level(con, where, res, out / base / f"r{res}", base)
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
    return meta
