# csb-census

An open, reproducible census of the crowdsourced bathymetry (CSB) that the IHO
Data Centre for Digital Bathymetry (DCDB) publishes through NOAA's public archive
(`s3://noaa-dcdb-bathymetry-pds/csb/csv/`).

For every provider, collection day and H3 cell it counts:

- soundings published
- **unique** soundings
- exact duplicates, split into resends and cross-platform repeats

It starts from the archive's first file in 2017 and follows NCEI's four daily
publication batches.

The census is independent. It is derived only from NOAA's public data, and it
does not imply IHO, DCDB, NOAA or provider endorsement. It is maintained by Long
Horizon Observatory alongside
[csb-trusted-node](https://github.com/lhzn-io/csb-trusted-node).

> **Status:** pre-alpha. The full-archive backfill is complete, and the 6-hourly
> updates are in their final validation run.

## Method in one paragraph

A sounding's identity is its `LON|LAT|DEPTH|TIME`, exactly as DCDB renders it.
The first-ingested copy is the original. Every later copy is a duplicate: a
*resend* if it carries the same platform ID, and *cross-platform* otherwise.
Ranking runs independently per collection month, because exact duplicates share
`TIME`. See [`docs/src/methodology.md`](docs/src/methodology.md).

## Terms

A **platform** is one `UNIQUE_ID` in the archive, the anonymous vessel ID a
Trusted Node issues (usually one vessel, not always). A **provider** is the
organization the data came through, usually a Trusted Node. A **vessel-day** is
one platform collecting in one place on one day. These and the census's other
terms are defined in [`docs/src/glossary.md`](docs/src/glossary.md), which the
dashboard also shows in its About drawer and page.

## Usage

```bash
uv sync --extra dev
uv run pytest
uv run csb-census backfill --start 2026-09-28 --end 2026-09-30 --out ./out
```

The backfill is resumable: completed ingest days and collection months are
recorded under `out/_done/`. Plain pip works too:
`pip install -e ".[dev]"`.

## License

MIT. See [LICENSE](LICENSE). The CSB data itself is published by NOAA NCEI on
behalf of the IHO DCDB.
