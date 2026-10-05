# Roadmap

## Phase 0: Method and backfill (current)

- [x] Identity, classification and partitioning defined (`docs/src/methodology.md`)
- [x] Resumable stage, rank and finalize pipeline with synthetic end-to-end tests
- [ ] Full-archive backfill (2017 onward)
- [ ] Validation: replay the incremental method over one month against the backfill

## Phase 1: Incremental updates

- [ ] 6-hourly GitHub Actions job after each NCEI batch (fingerprint check, then partial-overlap re-reads)
- [ ] Daily reconciliation of removed objects
- [ ] Published Parquet aggregates (release assets or Pages)

## Phase 2: Dashboard

- [ ] Static site: deck.gl H3 coverage maps, provider time series, community QA summary
- [ ] DuckDB-WASM queries over the published Parquet
- [ ] Regional views, starting with Long Island Sound at H3 resolution 8

## Phase 3: Gridded products

- [ ] Coverage cube (provider by month by lat/lon) as Zarr, compatible with topobathysim layers
