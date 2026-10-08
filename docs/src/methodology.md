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

The census uses two clocks and always says which (short definitions of every
term are in the [glossary](glossary.md)):

- **Publication time** is the stamp at the start of each file name, written when
  NCEI ingested the file. It answers "what came in today". Files reach the
  public bucket shortly after their stamp, usually in the next 6-hourly batch.
- **Collection time** is each sounding's own `TIME`. It answers "where were
  boats this week", and it drives every map.

**Publication lag** is the time from a file's newest sounding to its publication
stamp, measured per (file, `UNIQUE_ID`).

**Census latency** is how far the census runs behind NCEI: for each update that
takes in new files, the time from the newest of those files appearing in the
public bucket (its S3 publication time) to the update finishing. The pages show
the latest value and the median over the last 30 days, next to when the data
was last updated.

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
| `meta.json` | Totals: published, unique, resend, cross-platform, files, provider labels, platform IDs, vessel-days, platforms active in the last 30 days, the last ingest time, when the data was built, and the census latency (latest and 30-day median) |
| `timeseries_month.json` | The same counts per collection month |
| `recent.json` | Recent activity by publication time (last 24 hours, 7 and 30 days, and all time: files, soundings, platforms, new platforms, median lag), 6-hourly batches for 30 days, daily series by publication and by collection day for 400 days, the lag distribution, the census's own recent runs, and the last 24 hours' soundings by age at publication (under a week, a month, a year, older, no date) |
| `layers/r4`, `r6`, `r8` | All-time H3 cells at resolutions 4, 6 and 8 (finer levels split by parent cell), each with unique and published soundings, duplicate share, provider count, the first and last collection year, vessel-days and platforms |
| `layers/recent/7d`, `30d`, `365d` | Vessel-days and platforms per cell for soundings collected in the last 7, 30 or 365 days (the 365-day window stops at resolution 6) |
| `layers/recent/24h` | What NCEI published in the last 24 hours (by file stamp), at H3 resolutions 4 to 9: unique and published soundings and platforms per cell, the first and last collection day, and the soundings-weighted mean age at publication in days |
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

## What the census cannot see

The census counts what NCEI publishes. That is a floor, not a total, on the
crowdsourced bathymetry that exists:

- **Contributing to DCDB is a choice.** Logging tools such as
  [WIBL](https://github.com/CCOMJHC/WIBL) let a Trusted Node keep its data on its
  own servers or in its own cloud storage. Data ownership is sensitive in many
  places, and some collectors never submit.
- **Private collections are outside the archive.** Several recreational
  echosounder makers, and services such as Olex, gather depths from their users
  but share them only with subscribers.
- **Steps before NCEI are invisible.** Data held on a logger, waiting at a
  Trusted Node or rejected by a check before submission never reaches the
  census.
- **Valid is not good.** Trusted Nodes check that submissions are well formed
  (for example against the B-12 JSON schema in
  [csbschema](https://github.com/CCOMJHC/csbschema)). A well-formed file can
  still hold bad depths, positions or times. The census does not grade accuracy.

## Reading duplicates and lag

The census records *that* a duplicate exists, not *why*. What is known about
how data is collected suggests where to look, but these are hypotheses, not
findings:

- **Resends.** Builders of Trusted Node pipelines report that processing at
  scale is the easy part. The hard part is keeping track of which files have
  been sent, which succeeded and which failed. A pipeline that loses track will
  submit the same file again, which the census would count as a resend.
- **Vessel networks.** A logger records everything on the boat's network. Boats
  often carry more than one GPS or depth source, sometimes bridged between an
  old and a new network, so one vessel can broadcast several conflicting feeds.
  That makes for messy input, but it produces *differing* soundings, not exact
  copies, so it is not by itself an explanation for exact duplicates.
- **Lag.** Boats without internet at sea hand over data only when it is copied
  off the logger in port, often by phone, and a Trusted Node may then batch
  several vessels before submitting. Long and lumpy publication lag is expected,
  and a burst of old collection dates in one batch is normal.

## Limitations

- Near-duplicates (same platform and second with slightly different values) are
  not detected in this version.
- If DCDB changes its CSV rendering, the keys of newly published files change.
  This would show up as a sudden drop in duplicate rates.
- Counts describe what NCEI publishes, which can differ from what providers
  submit.
- Cross-provider duplicates are found only by rebaselines (see above).
