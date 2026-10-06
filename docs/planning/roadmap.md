# Roadmap

## Phase 0: Method and backfill

- [x] Identity, classification and partitioning defined (`docs/src/methodology.md`)
- [x] Resumable stage, rank and finalize pipeline with synthetic end-to-end tests
- [x] Full-archive backfill (2017 onward)
- [x] Re-rank with map cells (H3 resolution 9 per provider and month); measure state size
- [x] Validation: replay the incremental method over one month against the backfill

## Phase 1: Incremental updates

- [x] Fingerprint and candidate re-read classification, versioned state, reconcile (code and tests)
- [x] 6-hourly GitHub Actions workflow (schedule off until launch)
- [ ] Seed the state from the backfill and dry-run on garnet for three days
- [ ] Launch: publish the state release and enable the schedule

## Phase 2: Dashboard

- [x] Static site: Esri Ocean basemap, deck.gl H3 layers loaded by viewport, metrics, monthly chart
- [x] Duplicate-share view; provider views behind a build switch
- [x] Vessel-days (distinct platform and collection day per cell) as a map metric, in the state and layers
- [x] Landing page; recent-activity page (publication windows, 6-hourly batches, daily series, lag, recent-window map); dark basemap
- [ ] Map that follows any range brushed on the daily chart (needs layers partitioned by collection year)
- [ ] Long Island Sound polygon, resolution 9 and 10 layers, coverage summary
- [ ] Go live on GitHub Pages (after the DCDB/CIRES heads-up)

## Phase 3: Bathymetry context and gridded products

- [ ] Basemap mash-up: Open Waters Seascape and topobathykit (formerly topobathysim) tiles
- [ ] Crowdsourced depths compared with topobathykit survey surfaces in Long Island Sound, after tide reduction
- [ ] Coverage cube (provider by month by lat/lon) as Zarr, compatible with topobathykit layers
