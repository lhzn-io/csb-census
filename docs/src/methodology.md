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

## Vessel-days

A sounding count mostly reflects how long a logger ran and how fast its
echosounder pings: one boat logging at the dock for a season can outweigh a busy
harbor. To show where the crowd is, the census also counts **vessel-days**.

- A **platform** is a `UNIQUE_ID`, the only vessel identifier the archive carries.
- A vessel-day is one platform collecting in one place (an H3 resolution 8 cell,
  about 0.7 km²) on one UTC collection day.
- A vessel-day counts only if it holds soundings other than cross-platform
  duplicates, so data repeated under another platform ID adds no vessel.
- At coarser map levels, a cell's vessel-days are its distinct (platform, day)
  pairs, so a boat crossing a large cell in a day counts once.

## Two clocks

The census uses two clocks and always says which:

- **Publication time** is the stamp at the start of each file name, written when
  NCEI ingested the file. It answers "what came in today". Files reach the
  public bucket shortly after their stamp, usually in the next 6-hourly batch.
- **Collection time** is each sounding's own `TIME`. It answers "where were
  boats this week", and it drives every map.

**Publication lag** is the time from a file's newest sounding to its publication
stamp, measured per (file, `UNIQUE_ID`).

## File fingerprints

Each (file, `UNIQUE_ID`) gets a fingerprint: the row count plus the sum of the
low 64 bits of its sounding keys, modulo 2^64. A sum is used rather than an xor,
because an xor cancels out rows repeated within a file. Two files with equal
fingerprints are whole-file resends. Incremental updates use this to classify
most duplicates without re-reading earlier files.

## Backfill outputs

| File | Grain |
| :--- | :--- |
| `file_index.parquet` | One row per (file, `UNIQUE_ID`): provider, rows, time and bbox range, fingerprint, ingest time, unique and duplicate counts |
| `rank/file_counts/<month>.parquet` | (file, `UNIQUE_ID`) within one collection month: rows, unique, resend, cross-platform |
| `rank/daily/<month>.parquet` | (provider, collection day, H3 resolution 5 cell): rows, unique, resend, cross-platform, platforms |
| `rank/cells_r9/<month>.parquet` | (provider, collection month, H3 resolution 9 cell): rows, unique, resend, cross-platform |
| `rank/vdays_r8/<month>.parquet` | (provider, platform, collection day, H3 resolution 8 cell): rows, unique, resend, cross-platform |

## Incremental updates

After the backfill, the census follows NCEI's publication batches. Every 6 hours
(about 40 minutes after each batch) a job:

1. Lists the bucket from a few days before the newest file already counted. New
   files are (key, ETag) pairs not yet in the index. Each run handles at most a
   few thousand files, so catching up after an outage is spread over several runs.
2. Matches each new (file, `UNIQUE_ID`) group's fingerprint against every
   counted group. A match is a whole-file resend: all of its rows are
   duplicates, and nothing needs to be re-read.
3. Ranks every other new row against *candidate* files: earlier files from the
   same provider label that hold originals and overlap in time and bounding box.
   Candidates are re-read from the bucket.

Already-counted data always ranks ahead of new data. Online, the original is
therefore the **first published** copy; in the backfill it is the first
*ingested* copy. The two differ only when a file appears in the bucket after one
stamped later than it. Unique totals and map counts do not depend on which copy
is the original, because every copy shares one key and one position. Only which
file gets the credit, and the split between resend and cross-platform, can
differ.

A duplicate whose original came from a **different provider label** is not
detected online. Across the whole archive there were 4,873 such rows as of
2026-10-05 (0.0002%). Periodic rebaselines from the full archive recover them.

Once a day, a reconcile run compares the full bucket listing with the index:

- A file that has disappeared is marked removed, and its counts are subtracted.
  Map counts and vessel-days are subtracted exactly for files from the last 30
  days. Otherwise,
  and whenever a removed file held originals that later copies would inherit,
  the affected (provider, collection month) pairs are queued for a rebaseline.
- A file whose ETag has changed is treated as removed and then republished.

## Published data

The dashboard reads static files, so anyone can download and re-check them:

| File | Content |
| :--- | :--- |
| `meta.json` | Totals: published, unique, resend, cross-platform, files, provider labels, platform IDs, vessel-days, platforms active in the last 30 days, and the last ingest time |
| `timeseries_month.json` | The same counts per collection month |
| `recent.json` | Recent activity by publication time (last 24 hours, 7 and 30 days, and all time: files, soundings, platforms, new platforms, median lag), 6-hourly batches for 30 days, daily series by publication and by collection day for 400 days, the lag distribution, and the census's own recent runs |
| `layers/r4`, `r6`, `r8` | All-time H3 cells at resolutions 4, 6 and 8 (finer levels split by parent cell), each with unique and published soundings, duplicate share, provider count, the first and last collection year, vessel-days and platforms |
| `layers/recent/7d`, `30d`, `365d` | Vessel-days and platforms per cell for soundings collected in the last 7, 30 or 365 days (the 365-day window stops at resolution 6) |
| `layers/manifest.json` | Every layer file with its bounding box |
| `spilhaus/<center>/cells.json` | The resolution 4 layer projected into the world-ocean square of the landing page: Athelstan Spilhaus's aspect of Adams' world in a square (`classic`), and the same projection centered on the Americas, the Atlantic and the Pacific. Static land masks for each center (Natural Earth 1:50m) ship with the site |
| `lis/` | Long Island Sound at resolution 9: cells and a yearly coverage summary |

Figures shown per **provider label** describe NCEI's published archive under
that label. They measure publication, not provider behavior. A duplicate can
come from a provider resending data, from an ingest retry, or from a data-center
policy, and the census does not attribute the cause.

## Validation

Before publication, the incremental method is replayed over one month of
publication batches, starting from the census as of the month's first day, and
compared with the full backfill for that month. Both sides select files by the
timestamp in the file name. A file can be stamped before the month ends but
published by NCEI shortly after, so the replay runs one more batch past the end
of the month before comparing. They must agree within 0.01% on unique
soundings. The replay also reports the resend and cross-platform split, the
bytes re-read and the slowest run, which must finish within 45 minutes.

## Limitations

- Near-duplicates (same platform and second with slightly different values) are
  not detected in this version.
- If DCDB changes its CSV rendering, the keys of newly published files change.
  This would show up as a sudden drop in duplicate rates.
- Counts describe what NCEI publishes, which can differ from what providers
  submit.
- Cross-provider duplicates are found only by rebaselines (see above).
