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
| `src/csb_census/inventory.py` | Anonymous S3 listing and parallel, resumable download |
| `src/csb_census/pipeline.py` | `stage` (pass 1), `rank` (pass 2), `finalize`. The identity SQL is defined only here |
| `src/csb_census/cli.py` | `csb-census backfill` |
| `docs/src/methodology.md` | The public definition of the census. Keep it in sync with `pipeline.py` |

## Commands

- `uv sync --extra dev`, then `uv run pytest` (the tests download DuckDB's `h3` community extension once)
- `uv run pre-commit run --all-files`
- Long runs on garnet: run inside `tmux` under `caffeinate -s`, with
  `--memory-limit` at 32GB or less and `--threads` at 8 or fewer. Run the fleet
  preflight first (`uplift-agent-lab/AGENTS.md`).

## Conventions

- Python 3.12+, strict typing, uv, hatchling, ruff, and lines of 110 characters or fewer.
- Records go in Parquet. Gridded products, if any, go in Zarr.
- Identity is computed on DCDB's CSV text. Never parse numbers before hashing.
