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

- [x] Static site: OpenFreeMap basemaps (Dark, Positron), deck.gl H3 layers loaded by viewport, metrics, monthly chart
- [x] Duplicate-share view; provider views behind a build switch
- [x] Vessel-days (distinct platform and collection day per cell) as a map metric, in the state and layers
- [x] Landing page; recent-activity page (publication windows, 6-hourly batches, daily series, lag, recent-window map); dark basemap
- [x] Chart-room redesign in the Long Horizon brand (Josefin Sans, slate horizon band, hanging tabs, logbook drawer)
- [x] Landing page on the world-ocean square (Spilhaus, plus Americas, Atlantic and Pacific centers; nearest by time zone)
- [x] Public preview on Cloudflare Pages, deployed from garnet after each dry-run layers build
- [ ] Map that follows any range brushed on the daily chart (needs layers partitioned by collection year)
- [ ] Long Island Sound polygon, resolution 9 and 10 layers, coverage summary
- [ ] Go live on GitHub Pages (after the DCDB/CIRES heads-up)

## Phase 3: Bathymetry context and gridded products

- [ ] Basemap mash-up: Open Waters Seascape and topobathykit (formerly topobathysim) tiles
- [ ] Crowdsourced depths compared with topobathykit survey surfaces in the Long Island Sound, after tide reduction
- [ ] Coverage cube (provider by month by lat/lon) as Zarr, compatible with topobathykit layers

## Phase 4: The frontier (grading the crowd against Seabed 2030)

Volume and coverage say how busy the crowd is. Seabed 2030 asks a different
question: is the map getting *bigger*? Many soundings repeat the same harbors and
lanes, while the crowd's real value is boats that go where surveys have not.
This phase measures how much of the community's effort expands the map, and
grades it period by period. Grades are community-level (globally and by region),
never per provider, until the DCDB/CIRES heads-up is done.

- [ ] **Mapped, Seabed 2030 style.** A cell counts as mapped when it holds at
      least one sounding at the target resolution for its depth (Seabed 2030's
      depth-banded grid, roughly 100 m in water shallower than 1,500 m and
      coarser deeper down; confirm against the current spec). Approximate the
      bands with H3 resolutions (about r10 for the shallow band), with the depth
      band taken from GEBCO
- [ ] **First-seen state.** The first collection date and first publication date
      of every mapped cell, kept incrementally, so "new" is exact and cheap to
      update
- [ ] **Two frontiers.** *New to the archive*: the cell had no earlier CSB.
      *New to the map*: the cell is indirect in the GEBCO type-identifier (TID)
      grid (interpolated or predicted, not measured). The second is the one
      Seabed 2030 cares about
- [ ] **Frontier metrics per period:** area newly mapped (km², by depth band);
      frontier share (the fraction of the period's vessel-days that touched a
      new cell); concentration (the share of soundings in the busiest 1% of
      cells); repeat ratio (soundings per mapped cell)
- [ ] **Report card.** A grade for each month and year, globally and by region
      (EEZ, IHO sea area, and the Long Island Sound as the pilot), comparing
      expansion against volume. Is the crowd growing faster than the map?
- [ ] **Frontier map.** Cells colored by when they were first mapped, with
      "new this month" highlighted; a page that answers "where did the map grow?"
- [ ] **Method in `methodology.md`**, including the limits: CSB-only, a
      sounding is not a survey, and TID is only as current as the GEBCO
      release
