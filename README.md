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

> **Status:** pre-alpha. The method is fixed; the full-archive backfill and its
> validation are in progress.

## Method in one paragraph

A sounding's identity is its `LON|LAT|DEPTH|TIME`, exactly as DCDB renders it.
The first-ingested copy is the original. Every later copy is a duplicate: a
*resend* if it carries the same platform ID, and *cross-platform* otherwise.
Ranking runs independently per collection month, because exact duplicates share
`TIME`. See [`docs/src/methodology.md`](docs/src/methodology.md).

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
