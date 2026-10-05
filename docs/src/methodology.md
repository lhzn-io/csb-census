# Methodology

## Source

The source is NOAA's AWS Open Data bucket `noaa-dcdb-bathymetry-pds`, under the
prefix `csb/csv/<ingest year>/<month>/<day>/`. Each CSV row carries
`UNIQUE_ID, FILE_UUID, LON, LAT, DEPTH, TIME, PLATFORM_NAME, PROVIDER`. File
names begin with a UTC ingest stamp (`YYYYMMDDhhmmss` followed by fractional
digits).

As of 2026-10-05 the archive held 1,000,684 files (446.8 GB). NCEI publishes in
four batches a day, at 00, 06, 12 and 18 UTC.

## Identity

- A **sounding key** is the MD5 of `LON|LAT|DEPTH|TIME`, computed over DCDB's own
  CSV text. Fields are read as text and never re-formatted as floats, so the key
  is stable across tools and versions.
- `UNIQUE_ID` is deliberately **not** part of the key. The same observations
  have been published under more than one platform ID, and an ID-scoped key
  would count them twice.

## Classification

Within each set of identical keys, the copy in the earliest-ingested file is the
**original**, with ties broken by file name. Every later copy is one of:

| Class | Rule |
| :--- | :--- |
| `dup_resend` | Same `UNIQUE_ID` as the original |
| `dup_cross_id` | Different `UNIQUE_ID` from the original |

Unique soundings are the originals. Both duplicate classes are reported,
because they describe different issues: delivery (resends) and platform
identity (cross-platform repeats).

## Partitioning

Every exact duplicate shares its `TIME` value, so ranking is independent per
collection month (the first 7 characters of `TIME`). Rows whose `TIME` is not an
ISO date go to an `invalid` partition. They are counted, but not mapped.

## File fingerprints

Each (file, `UNIQUE_ID`) gets a fingerprint: the row count plus the sum of the
low 64 bits of its sounding keys, modulo 2^64. A sum is used rather than an xor,
because an xor cancels out rows repeated within a file. Two files with equal
fingerprints are whole-file resends. Incremental updates use this to classify
most duplicates without re-reading earlier files.

## Outputs

| File | Grain |
| :--- | :--- |
| `file_index.parquet` | One row per (file, `UNIQUE_ID`): provider, rows, time and bbox range, fingerprint, ingest time, unique and duplicate counts |
| `rank/daily/<month>.parquet` | (provider, collection day, H3 resolution 5 cell): rows, unique, resend, cross-platform, platforms |

## Validation

Before publication, the 6-hourly incremental method is replayed over one month
and compared with the full backfill for that month. They must agree within 0.01%
on unique soundings.

## Limitations

- Near-duplicates (same platform and second with slightly different values) are
  not detected in this version.
- If DCDB changes its CSV rendering, the keys of newly published files change.
  This would show up as a sudden drop in duplicate rates.
- Counts describe what NCEI publishes, which can differ from what providers
  submit.
