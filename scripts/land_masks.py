"""Build the landing page's land masks, one per center of the world-ocean square.

Land never changes, so the masks are static site assets (site/public/spilhaus/<center>/land.json),
rebuilt only if the centers or the source change. Run once, with Natural Earth 1:50m land
(public domain; https://www.naturalearthdata.com/, file ne_50m_land.shp):

    uv run --with shapely --with pyshp python scripts/land_masks.py path/to/ne_50m_land.shp

Each mask samples the square on a 1000 x 1000 grid, maps every sample back to longitude and
latitude (the inverse projection), and run-length encodes the land per row: [start, length, ...].
"""

import json
import sys
from pathlib import Path

import numpy as np
import shapefile  # pyshp
from pyproj import Transformer
from shapely import contains_xy, prepare
from shapely.geometry import shape
from shapely.ops import unary_union

from csb_census.spilhaus import CENTERS, HALF_WIDTH, crs

N = 1000
SITE = Path(__file__).resolve().parents[1] / "site" / "public" / "spilhaus"


def main(shp: Path) -> None:
    land = unary_union([shape(s.__geo_interface__) for s in shapefile.Reader(str(shp)).shapes()])
    prepare(land)
    centers = (np.arange(N) + 0.5) / N
    gx, gy = np.meshgrid(-HALF_WIDTH + 2 * HALF_WIDTH * centers, HALF_WIDTH - 2 * HALF_WIDTH * centers)
    for center in CENTERS:
        inv = Transformer.from_crs(crs(center), "EPSG:4326", always_xy=True)
        lon, lat = (np.asarray(a) for a in inv.transform(gx.ravel(), gy.ravel()))
        inside = np.isfinite(lon) & np.isfinite(lat)  # the square's corners have no inverse
        is_land = np.zeros(N * N, dtype=bool)
        is_land[inside] = contains_xy(land, lon[inside], lat[inside])
        runs = []
        for row in is_land.reshape(N, N):
            d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
            starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
            runs.append([int(v) for pair in zip(starts, ends - starts, strict=True) for v in pair])
        dest = SITE / center
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "land.json").write_text(json.dumps({"size": N, "runs": runs}, separators=(",", ":")) + "\n")
        print(f"{center}: {is_land.mean():.1%} land")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
