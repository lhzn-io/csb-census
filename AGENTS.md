# AGENTS.md: csb-census

Repo-specific context for AI developer agents. Global fleet rules live in
`$HOME/.agents/global-rules.md`.

## Purpose

An independent, reproducible census of DCDB-published crowdsourced bathymetry.
It is the community-measurement companion to `lhzn-io/csb-trusted-node`.

## Planning ledger (public repo)

- `docs/planning/roadmap.md` is the only planning file kept here.
- `task.md`, `implementation_plan.md` and `walkthrough.md` live in the internal
  `lhzn-io/unh-ccom-agent-lab` under `docs/planning/csb-census/`. They are
  gitignored here.
- **Etiquette:** never publish per-provider QA findings (duplicate rates, bad
  timestamps) in this repo until the provider and DCDB/CIRES have been told
  privately. Methodology and community-level figures are fine.

## Layout

| Path | Role |
| :--- | :--- |
| `src/csb_census/inventory.py` | Anonymous S3 listing and download over keep-alive connections, with retries; `S3Source` / `LocalSource` |
| `src/csb_census/pipeline.py` | `read_keyed`, `stage` (pass 1), `rank` (pass 2), `finalize`. The identity SQL is defined only here |
| `src/csb_census/state.py` | Versioned state (Parquet tables plus manifest), mirrored to the `state` release with a generation check |
| `src/csb_census/incremental.py` | `seed`, `run` (6-hourly), `reconcile` (daily), `AsOfSource` for replays |
| `src/csb_census/layers.py` | Published dashboard data: H3 layers, manifest, `meta.json`, `timeseries_month.json` |
| `src/csb_census/lis.py` | Regional analysis (Long Island Sound): r9 layer and summary; `extract` of originals at r10 on garnet |
| `src/csb_census/cli.py` | `backfill`, `rank`, `seed`, `incremental`, `reconcile`, `replay`, `state pull/push`, `layers`, `lis extract` |
| `site/` | Dashboard (Vite + TypeScript, MapLibre 5, deck.gl 9, hyparquet). Data in `site/public/data` (gitignored) |
| `.github/workflows/update.yml` | 6-hourly incremental, daily reconcile, Pages deploy. **Schedule stays commented out until launch** |
| `docs/src/methodology.md` | The public definition of the census. Keep it in sync with `pipeline.py` and `incremental.py` |
| `tests/synthetic.py` | The synthetic archive (files A to H) covering every duplicate pattern; shared by most tests |

## Commands

- `uv sync --extra dev`, then `uv run pytest` (the tests download DuckDB's `h3` community extension once)
- `uv run pre-commit run --all-files`
- Dashboard preview: `uv run csb-census layers --state <state> --out site/public/data`, then
  `npm --prefix site run dev`. MapLibre stays on 5.x: deck.gl 9.4's interleaved overlay fails on 6.x.
- Provider views (`layers --providers`, `VITE_SHOW_PROVIDERS=true`) stay off until the DCDB/CIRES
  heads-up has happened; the repo variable `SHOW_PROVIDERS` switches them on in the workflow.
- Long runs on garnet: run inside `tmux` under `caffeinate -s`, with
  `--memory-limit` at 32GB or less and `--threads` at 8 or fewer. Run the fleet
  preflight first (`uplift-agent-lab/AGENTS.md`).

## Conventions

- Python 3.12+, strict typing, uv, hatchling, ruff, and lines of 110 characters or fewer.
- Records go in Parquet. Gridded products, if any, go in Zarr.
- Identity is computed on DCDB's CSV text. Never parse numbers before hashing.
