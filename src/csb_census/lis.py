"""Regional analysis for a polygon, built first for Long Island Sound (LIS).

Two products:

* ``build`` (anywhere, from the census state): ``<out>/r9.parquet`` with the same columns as the
  map layers, restricted to the region, and ``<out>/summary.json`` with coverage per collection
  year. Coverage is measured against the region's own H3 r9 cells, so "share covered" means the
  share of the region's cells holding at least 1 (or 100) unique soundings.
* ``extract`` (garnet, from staged soundings): ``<out>/r10.parquet`` and an internal
  ``<out>/soundings.parquet`` with every original sounding in the region and how many copies it
  has. That table is the hook for comparing crowdsourced depths with surveys after tide
  reduction; it joins on the sounding key.

A sounding falls inside the region when its H3 cell (r9 or r10) has its centre inside the
polygon (H3's polyfill rule), so edges are resolved to about one cell (174 m at r9, 66 m at r10).
"""

import json
from pathlib import Path
from typing import Any

import duckdb

from csb_census.layers import load_cells
from csb_census.pipeline import CELL_RES, h3_cell
from csb_census.state import State


def _p(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _ring(coords: list[list[float]]) -> str:
    return "(" + ", ".join(f"{x} {y}" for x, y, *_ in coords) + ")"


def polygon_wkts(geojson: Path) -> list[str]:
    """Every polygon in a GeoJSON file (Feature, FeatureCollection or bare geometry) as WKT."""
    doc = json.loads(geojson.read_text(encoding="utf-8"))
    geoms: list[dict[str, Any]] = []
    if doc.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in doc["features"]]
    elif doc.get("type") == "Feature":
        geoms = [doc["geometry"]]
    else:
        geoms = [doc]
    wkts = []
    for g in geoms:
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        if g["type"] not in ("Polygon", "MultiPolygon"):
            raise ValueError(f"unsupported geometry {g['type']}")
        wkts += ["POLYGON (" + ", ".join(_ring(r) for r in rings) + ")" for rings in polys]
    if not wkts:
        raise ValueError(f"no polygons in {geojson}")
    return wkts


def region_cells(con: duckdb.DuckDBPyConnection, wkts: list[str], res: int, table: str) -> int:
    con.sql(f"CREATE OR REPLACE TEMP TABLE {table} (h3 UBIGINT)")
    for wkt in wkts:
        con.execute(
            f"INSERT INTO {table} SELECT DISTINCT unnest(h3_polygon_wkt_to_cells($wkt, {int(res)}))",
            {"wkt": wkt},
        )
    con.sql(f"CREATE OR REPLACE TEMP TABLE {table} AS SELECT DISTINCT h3 FROM {table}")
    row = con.sql(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def build(con: duckdb.DuckDBPyConnection, state: State, polygon: Path, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    n_cells = region_cells(con, polygon_wkts(polygon), CELL_RES, "region")
    load_cells(con, state)
    con.sql("CREATE OR REPLACE TEMP TABLE rc AS SELECT c.* FROM cells c SEMI JOIN region r ON r.h3 = c.h3")
    con.sql(
        f"""
        COPY (
          SELECT h3_h3_to_string(h3) AS h3,
                 sum(n_unique)::BIGINT AS n_unique, sum(n_rows)::BIGINT AS n_published,
                 round(1 - sum(n_unique) / sum(n_rows), 4) AS dup_share,
                 count(DISTINCT provider)::INTEGER AS n_providers,
                 min(left(coll_month, 4))::INTEGER AS first_year,
                 max(left(coll_month, 4))::INTEGER AS last_year
          FROM rc GROUP BY h3 ORDER BY h3
        ) TO '{_p(out / f"r{CELL_RES}.parquet")}' (FORMAT parquet, COMPRESSION snappy)
        """
    )
    area = con.sql("SELECT sum(h3_cell_area(h3, 'km^2')) FROM region").fetchone()
    region_km2 = float(area[0]) if area and area[0] else 0.0

    def coverage(where: str) -> dict[str, Any]:
        row = con.sql(
            f"""
            WITH per AS (
              SELECT h3, sum(n_unique) AS u, sum(n_rows) AS p, count(DISTINCT provider) AS prov
              FROM rc {where} GROUP BY h3
            )
            SELECT coalesce(sum(u), 0)::BIGINT, coalesce(sum(p), 0)::BIGINT,
                   count(*) FILTER (WHERE u >= 1), count(*) FILTER (WHERE u >= 100),
                   coalesce(sum(h3_cell_area(h3, 'km^2')) FILTER (WHERE u >= 1), 0)
            FROM per
            """
        ).fetchone()
        assert row is not None
        prov = con.sql(f"SELECT count(DISTINCT provider) FROM rc {where}").fetchone()
        return {
            "unique": int(row[0]),
            "published": int(row[1]),
            "providers": int(prov[0]) if prov else 0,
            "cells_ge1": int(row[2]),
            "cells_ge100": int(row[3]),
            "km2_covered": round(float(row[4]), 2),
            "share_ge1": round(row[2] / n_cells, 4) if n_cells else 0.0,
            "share_ge100": round(row[3] / n_cells, 4) if n_cells else 0.0,
        }

    years = [y for (y,) in con.sql("SELECT DISTINCT left(coll_month, 4) FROM rc ORDER BY 1").fetchall()]
    summary = {
        "polygon": polygon.name,
        "resolution": CELL_RES,
        "region_cells": n_cells,
        "region_km2": round(region_km2, 2),
        "all_time": coverage(""),
        "by_year": {y: coverage(f"WHERE left(coll_month, 4) = '{y}'") for y in years},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def extract(
    con: duckdb.DuckDBPyConnection, stage_root: Path, polygon: Path, out: Path, *, res: int = 10
) -> int:
    """Originals inside the region at ``res`` from staged soundings (garnet), plus their copy counts."""
    out.mkdir(parents=True, exist_ok=True)
    wkts = polygon_wkts(polygon)
    region_cells(con, wkts, res, "region_fine")
    bbox = con.sql(
        """
        SELECT min(h3_cell_to_lng(h3)) - 0.01, min(h3_cell_to_lat(h3)) - 0.01,
               max(h3_cell_to_lng(h3)) + 0.01, max(h3_cell_to_lat(h3)) + 0.01
        FROM region_fine
        """
    ).fetchone()
    assert bbox is not None and bbox[0] is not None, "polygon produced no cells"
    w, s, e, n = bbox
    src = _p(stage_root / "soundings" / "*" / "*.parquet")
    con.sql(
        f"""
        CREATE OR REPLACE TEMP TABLE fine AS
        SELECT * FROM (
          SELECT *, {h3_cell(res)} AS h3
          FROM read_parquet('{src}', hive_partitioning = false)
          WHERE try_cast(LON AS DOUBLE) BETWEEN {w} AND {e} AND try_cast(LAT AS DOUBLE) BETWEEN {s} AND {n}
        ) f SEMI JOIN region_fine r ON r.h3 = f.h3
        """
    )
    con.sql(
        f"""
        COPY (
          SELECT key, PROVIDER AS provider, UNIQUE_ID AS unique_id, TIME AS time,
                 try_cast(LON AS DOUBLE) AS lon, try_cast(LAT AS DOUBLE) AS lat,
                 try_cast(DEPTH AS DOUBLE) AS depth,
                 h3_h3_to_string(h3) AS h3_r{res}, n_copies, file AS first_file
          FROM (
            SELECT *, count(*) OVER (PARTITION BY key) AS n_copies,
                   row_number() OVER (PARTITION BY key ORDER BY ingested, file) AS rn
            FROM fine
          ) WHERE rn = 1 ORDER BY time
        ) TO '{_p(out / "soundings.parquet")}' (FORMAT parquet, COMPRESSION zstd)
        """
    )
    con.sql(
        f"""
        COPY (
          SELECT h3_h3_to_string(h3) AS h3, count(DISTINCT key)::BIGINT AS n_unique,
                 count(*)::BIGINT AS n_published, round(1 - count(DISTINCT key) / count(*), 4) AS dup_share,
                 count(DISTINCT PROVIDER)::INTEGER AS n_providers,
                 min(left(TIME, 4))::INTEGER AS first_year, max(left(TIME, 4))::INTEGER AS last_year
          FROM fine GROUP BY h3 ORDER BY h3
        ) TO '{_p(out / f"r{res}.parquet")}' (FORMAT parquet, COMPRESSION snappy)
        """
    )
    row = con.sql("SELECT count(DISTINCT key) FROM fine").fetchone()
    return int(row[0]) if row else 0
