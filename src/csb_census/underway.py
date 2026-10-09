"""Tracks and underway time: how long vessels log while moving, and while stationary.

Each (file, platform) group is a track. Its soundings are averaged into 1-minute centroids, and each
minute is classed by the speed between it and the previous minute of the same track (or the next
minute, for the first of a segment). Minutes further apart than ``MAX_GAP_S`` start a new segment.
Single GPS fixes jitter too much for point-to-point speeds; minute centroids smooth that out.

Only groups holding at least one unique sounding count, so a whole-file resend never doubles the
time. The census keeps the minutes rolled up per (provider, platform, collection day, H3 r8 cell).
"""

import math
from pathlib import Path

import duckdb

from csb_census.pipeline import read_keyed

BANDS = (("stationary", 0.5), ("slow", 2.0), ("underway", math.inf))  # knots, upper bounds
MAX_GAP_S = 300
GLITCH_KN = 60  # faster than this between minute centroids is a position glitch, not a speed
UW_RES = 8
MINUTE_COLUMNS = ("min_underway", "min_slow", "min_stationary", "min_other")
UW_COLUMNS = (
    f"provider VARCHAR, platform VARCHAR, day VARCHAR, h3_r{UW_RES} UBIGINT, "
    "min_underway BIGINT, min_slow BIGINT, min_stationary BIGINT, min_other BIGINT, n_soundings BIGINT"
)


def _knots(lat0: str, lon0: str, lat1: str, lon1: str, seconds: str) -> str:
    """Great-circle distance between two points over a time, in knots."""
    return (
        f"2 * 6371000 * asin(sqrt(pow(sin(radians({lat1} - {lat0}) / 2), 2) + cos(radians({lat0})) "
        f"* cos(radians({lat1})) * pow(sin(radians({lon1} - {lon0}) / 2), 2))) / ({seconds}) * 1.943844"
    )


def minutes_sql(rows: str, keep: str) -> str:
    """One row per (file, platform, minute) with its centroid, sounding count, speed and band.

    ``rows`` is a table from ``read_keyed``; ``keep`` holds the (file, unique_id) groups to count.
    Soundings repeated within a file count once; rows without a usable time or position are skipped.
    """
    bands = " ".join(f"WHEN kn < {hi} THEN '{name}'" for name, hi in BANDS if math.isfinite(hi))
    last = BANDS[-1][0]
    back = _knots("lat0", "lon0", "lat", "lon", "epoch(m) - epoch(m0)")
    ahead = _knots("lat", "lon", "lat1", "lon1", "epoch(m1) - epoch(m)")
    return f"""
        WITH pts AS (
          SELECT DISTINCT r.file, r.UNIQUE_ID AS platform, r.PROVIDER AS provider, r.key,
                 try_strptime(left(r.TIME, 19), '%Y-%m-%dT%H:%M:%S') AS t,
                 try_cast(r.LON AS DOUBLE) AS lon, try_cast(r.LAT AS DOUBLE) AS lat
          FROM {rows} r SEMI JOIN {keep} k ON k.file = r.file AND k.unique_id = r.UNIQUE_ID
        ), mins AS (
          SELECT file, platform, any_value(provider) AS provider, time_bucket(INTERVAL 1 MINUTE, t) AS m,
                 count(*) AS n, avg(lon) AS lon, avg(lat) AS lat
          FROM pts
          WHERE t IS NOT NULL AND lon BETWEEN -180 AND 180 AND lat BETWEEN -90 AND 90
          GROUP BY file, platform, m
        ), w AS (
          SELECT *, lag(m) OVER tr AS m0, lag(lon) OVER tr AS lon0, lag(lat) OVER tr AS lat0,
                 lead(m) OVER tr AS m1, lead(lon) OVER tr AS lon1, lead(lat) OVER tr AS lat1
          FROM mins WINDOW tr AS (PARTITION BY file, platform ORDER BY m)
        ), s AS (
          SELECT *, CASE WHEN m0 IS NOT NULL AND epoch(m) - epoch(m0) <= {MAX_GAP_S} THEN {back}
                         WHEN m1 IS NOT NULL AND epoch(m1) - epoch(m) <= {MAX_GAP_S} THEN {ahead} END AS kn
          FROM w
        )
        SELECT file, platform, provider, m, n::INTEGER AS n, lon, lat, kn::FLOAT AS kn,
               CASE WHEN kn IS NULL THEN 'isolated' WHEN kn >= {GLITCH_KN} THEN 'glitch'
                    {bands} ELSE '{last}' END AS band
        FROM s
    """


def underway_sql(minutes: str, *, with_file: bool = False) -> str:
    """Minutes rolled up per (provider, platform, collection day, H3 r8 of the minute centroid)."""
    file_col = "file, " if with_file else ""
    return f"""
        SELECT {file_col}provider, platform, strftime(m, '%Y-%m-%d') AS day,
               h3_latlng_to_cell(lat, lon, {UW_RES}) AS h3_r{UW_RES},
               count(*) FILTER (WHERE band = 'underway') AS min_underway,
               count(*) FILTER (WHERE band = 'slow') AS min_slow,
               count(*) FILTER (WHERE band = 'stationary') AS min_stationary,
               count(*) FILTER (WHERE band IN ('isolated', 'glitch')) AS min_other,
               sum(n)::BIGINT AS n_soundings
        FROM {minutes} GROUP BY ALL
    """


def keep_groups_sql(index: str, files: str | None = None) -> str:
    """(file, unique_id) groups with at least one unique sounding, from a file index table or path."""
    within = f"SEMI JOIN {files} f ON f.file = i.file" if files else ""
    return f"SELECT i.file, i.unique_id FROM {index} i {within} WHERE i.status = 'live' AND i.n_unique > 0"


def classify_files(con: duckdb.DuckDBPyConnection, paths: list[Path], keep: str, dest: Path) -> None:
    """Read CSVs with the census's reader and write their minute rows to ``dest`` (Parquet)."""
    read_keyed(con, paths, "uw_raw")
    part = dest.with_suffix(".part")
    con.sql(f"COPY ({minutes_sql('uw_raw', keep)}) TO '{part.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    con.sql("DROP TABLE uw_raw")
    part.replace(dest)
