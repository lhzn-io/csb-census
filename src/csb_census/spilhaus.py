"""The landing map: every archive cell projected into the world-ocean square, for each center offered.

The square is PROJ's ``spilhaus`` projection: Adams' world in a square at the aspect Athelstan
Spilhaus chose, which keeps every ocean joined and puts the cuts through land. The other centers
move that aspect (``+lat_0``, ``+lon_0``; ``+azi=45`` keeps north up) so a viewer can start near home.

Writes ``<out>/spilhaus/<center>/cells.json`` with columns of equal length: ``u``, ``v`` (0..1 from
the square's top-left), ``lat``, ``lon`` and the cell's counts. The matching land masks never change,
so they are static site assets (``site/public/spilhaus/<center>/land.json``, from ``scripts/land_masks.py``).
"""

import json
import math
from pathlib import Path
from typing import Any

import duckdb
from pyproj import Transformer

CENTERS: dict[str, str] = {
    "spilhaus": "",
    "americas": "+lat_0=15 +lon_0=-75 +azi=45",
    "atlantic": "+lat_0=40 +lon_0=-45 +azi=45",
    "pacific": "+lat_0=15 +lon_0=-165 +azi=45",
}
HALF_WIDTH = 11_802_700.0  # metres from the center to each edge of the square (the same for every center)
COLUMNS = ("lat", "lon", "unique", "published", "vessel_days", "platforms", "first_year", "last_year")


def crs(center: str) -> str:
    return f"+proj=spilhaus +ellps=WGS84 {CENTERS[center]}".strip()


def to_square(center: str, lon: list[float], lat: list[float]) -> tuple[list[float], list[float]]:
    """Project longitudes and latitudes into (u, v), both 0..1 from the square's top-left corner."""
    x, y = Transformer.from_crs("EPSG:4326", crs(center), always_xy=True).transform(lon, lat)
    u = [(xi + HALF_WIDTH) / (2 * HALF_WIDTH) for xi in x]
    v = [(HALF_WIDTH - yi) / (2 * HALF_WIDTH) for yi in y]
    return u, v


def write_cells(con: duckdb.DuckDBPyConnection, r4_glob: str, out: Path) -> dict[str, int]:
    """Project the archive's H3 r4 layer for every center; returns the cells written per center."""
    rows = con.sql(
        f"""SELECT h3_cell_to_lat(h3_string_to_h3(h3)) AS lat, h3_cell_to_lng(h3_string_to_h3(h3)) AS lon,
                   n_unique, n_published, vessel_days, platforms, first_year, last_year
            FROM read_parquet('{r4_glob}') ORDER BY n_unique, h3"""
    ).fetchall()
    cols: dict[str, list[Any]] = {name: [r[i] for r in rows] for i, name in enumerate(COLUMNS)}
    written = {}
    for center in CENTERS:
        u, v = to_square(center, cols["lon"], cols["lat"])
        keep = [i for i in range(len(u)) if math.isfinite(u[i]) and math.isfinite(v[i])]
        doc: dict[str, list[Any]] = {"u": [round(u[i], 5) for i in keep], "v": [round(v[i], 5) for i in keep]}
        doc["lat"] = [round(cols["lat"][i], 3) for i in keep]
        doc["lon"] = [round(cols["lon"][i], 3) for i in keep]
        for name in COLUMNS[2:]:
            doc[name] = [int(cols[name][i]) for i in keep]
        dest = out / "spilhaus" / center
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "cells.json").write_text(json.dumps(doc, separators=(",", ":")))
        written[center] = len(keep)
    return written
