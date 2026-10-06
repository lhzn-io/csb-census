"""``csb-census`` command-line entry point."""

import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import click
import duckdb

from csb_census import __version__, incremental, layers, lis, pipeline
from csb_census import state as state_mod
from csb_census.inventory import S3Source, day_prefix, download, list_objects
from csb_census.state import State

log = logging.getLogger("csb_census")


@click.group()
@click.version_option(__version__)
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Census of crowdsourced bathymetry published by the IHO DCDB."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


@cli.command()
@click.option("--start", type=click.DateTime(["%Y-%m-%d"]), required=True, help="First ingest day.")
@click.option("--end", type=click.DateTime(["%Y-%m-%d"]), required=True, help="Last ingest day.")
@click.option("--out", type=click.Path(path_type=Path), required=True, help="Output directory.")
@click.option("--work", type=click.Path(path_type=Path), help="Scratch for downloads and spill.")
@click.option("--memory-limit", default="32GB", show_default=True)
@click.option("--threads", default=8, show_default=True)
@click.option("--download-workers", default=16, show_default=True)
@click.option("--keep-csv", is_flag=True, help="Keep downloaded CSVs after staging.")
def backfill(
    start: datetime,
    end: datetime,
    out: Path,
    work: Path | None,
    memory_limit: str,
    threads: int,
    download_workers: int,
    keep_csv: bool,
) -> None:
    """Stage ingest days, rank every collection month, and write the file index. Resumable."""
    work = work or out / "_work"
    con = pipeline.connect(memory_limit=memory_limit, threads=threads, temp_dir=work / "spill")

    for day in _days(start.date(), end.date()):
        batch = f"{day:%Y%m%d}"
        if pipeline.is_done(out, f"stage_{batch}"):
            continue
        objects = list(list_objects(day_prefix(day)))
        if objects:
            t0 = time.time()
            csv_dir = work / "csv" / batch
            paths = download(objects, csv_dir, workers=download_workers)
            t1 = time.time()
            result = pipeline.stage(con, paths, out, batch)
            mb = sum(o.size for o in objects) / 1e6
            log.info(
                "stage %s: %d files, %d rows, %.0f MB (download %.0fs, stage %.0fs)",
                batch,
                result.files,
                result.rows,
                mb,
                t1 - t0,
                time.time() - t1,
            )
            if not keep_csv:
                shutil.rmtree(csv_dir, ignore_errors=True)
        pipeline.mark_done(out, f"stage_{batch}")

    for month in pipeline.staged_months(out):
        if pipeline.is_done(out, f"rank_{month}"):
            continue
        t0 = time.time()
        pipeline.rank(con, out, month)
        pipeline.mark_done(out, f"rank_{month}")
        log.info("rank %s: %.0fs", month, time.time() - t0)

    index = pipeline.finalize(con, out)
    log.info("wrote %s", index)


def _connect(ctx_opts: dict[str, object], work: Path | None = None) -> duckdb.DuckDBPyConnection:
    return pipeline.connect(
        memory_limit=str(ctx_opts["memory_limit"]),
        threads=int(str(ctx_opts["threads"])),
        temp_dir=work / "spill" if work else None,
    )


def engine_options[F: Callable[..., Any]](f: F) -> F:
    f = click.option("--memory-limit", default="10GB", show_default=True)(f)
    f = click.option("--threads", default=4, show_default=True)(f)
    return f


def _utc(value: datetime | None) -> datetime:
    """Click parses naive datetimes; the census treats every timestamp as UTC."""
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@cli.command()
@click.option("--out", type=click.Path(path_type=Path), required=True, help="Backfill directory with stage/.")
@click.option("--before", type=click.DateTime(), help="Only files ingested before this UTC instant.")
@click.option("--dest", type=click.Path(path_type=Path), help="Write rank/ and file_index here instead.")
@engine_options
def rank(out: Path, before: datetime | None, dest: Path | None, memory_limit: str, threads: int) -> None:
    """Re-rank every staged collection month (all outputs, including map cells), then finalize."""
    con = _connect({"memory_limit": memory_limit, "threads": threads}, dest or out)
    for month in pipeline.staged_months(out):
        t0 = time.time()
        pipeline.rank(con, out, month, before=before, dest=dest)
        if dest is None:
            pipeline.mark_done(out, f"rank_{month}")
        log.info("rank %s: %.0fs", month, time.time() - t0)
    log.info("wrote %s", pipeline.finalize(con, out, before=before, dest=dest))


@cli.command()
@click.option("--backfill", "backfill_dir", type=click.Path(path_type=Path), required=True)
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@engine_options
def seed(backfill_dir: Path, state_dir: Path, memory_limit: str, threads: int) -> None:
    """Create state generation 1 from a finished backfill and a full bucket listing."""
    work = state_dir.parent / f"{state_dir.name}-work"
    con = _connect({"memory_limit": memory_limit, "threads": threads}, work)
    gen = incremental.seed(con, backfill_dir, State(state_dir), S3Source(), work=work)
    log.info("seeded generation %d", gen)


@cli.command(name="incremental")
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@click.option("--repo", help="Pull before and push after, using this repo's 'state' release.")
@click.option("--now", type=click.DateTime(), help="UTC instant to run as (default: now).")
@click.option("--max-files", default=4000, show_default=True)
@click.option("--max-bytes", default=3_000_000_000, show_default=True)
@engine_options
def incremental_cmd(
    state_dir: Path,
    repo: str | None,
    now: datetime | None,
    max_files: int,
    max_bytes: int,
    memory_limit: str,
    threads: int,
) -> None:
    """Classify everything published since the last run and commit a new state generation."""
    state = State(state_dir)
    if repo:
        state_mod.pull(state, repo)
    when = _utc(now)
    con = _connect({"memory_limit": memory_limit, "threads": threads})
    result = incremental.run(
        con,
        state,
        S3Source(),
        now=when,
        run_id=f"{when:%Y%m%dT%H%M}",
        max_files=max_files,
        max_bytes=max_bytes,
    )
    click.echo(json.dumps(asdict(result)))
    if repo and result.new_files:
        state_mod.push(state, repo)


@cli.command()
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@click.option("--repo", help="Pull before and push after, using this repo's 'state' release.")
@engine_options
def reconcile(state_dir: Path, repo: str | None, memory_limit: str, threads: int) -> None:
    """Compare a full listing with the index: removals, republished files and missed keys."""
    state = State(state_dir)
    if repo:
        state_mod.pull(state, repo)
    work = state_dir.parent / f"{state_dir.name}-work"
    con = _connect({"memory_limit": memory_limit, "threads": threads}, work)
    result = incremental.reconcile(con, state, S3Source(), now=datetime.now(UTC), work=work)
    click.echo(json.dumps(asdict(result)))
    if repo:
        state_mod.push(state, repo)


@cli.command()
@click.option("--backfill", "backfill_dir", type=click.Path(path_type=Path), required=True)
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True, help="Fresh directory.")
@click.option("--start", type=click.DateTime(), required=True, help="Replay from this UTC instant.")
@click.option("--end", type=click.DateTime(), required=True, help="Replay up to this UTC instant.")
@click.option("--step-hours", default=6, show_default=True)
@click.option("--tolerance", default=0.0001, show_default=True, help="Allowed relative unique difference.")
@engine_options
def replay(
    backfill_dir: Path,
    state_dir: Path,
    start: datetime,
    end: datetime,
    step_hours: int,
    tolerance: float,
    memory_limit: str,
    threads: int,
) -> None:
    """Validation gate: seed as of START, replay publication batches to END, compare with the backfill."""
    t_start, t_end = _utc(start), _utc(end)
    asof = state_dir.parent / f"{state_dir.name}-asof"
    work = state_dir.parent / f"{state_dir.name}-work"
    con = _connect({"memory_limit": memory_limit, "threads": threads}, work)
    naive = t_start.replace(tzinfo=None)
    for month in pipeline.staged_months(backfill_dir):
        pipeline.rank(con, backfill_dir, month, before=naive, dest=asof)
    pipeline.finalize(con, backfill_dir, before=naive, dest=asof)
    state = State(state_dir)
    s3 = S3Source()
    incremental.seed(con, asof, state, incremental.AsOfSource(s3, t_start), work=work)

    slowest, reread = 0.0, 0
    t = t_start
    while t < t_end:
        t = min(t + timedelta(hours=step_hours), t_end)
        t0 = time.time()
        result = incremental.run(
            con, state, incremental.AsOfSource(s3, t), now=t, run_id=f"replay-{t:%Y%m%dT%H}"
        )
        slowest, reread = max(slowest, time.time() - t0), reread + result.reread_bytes
        log.info("replay %s: %s (%.0fs)", t.isoformat(), asdict(result), time.time() - t0)

    window = (
        f"ingested >= TIMESTAMP '{naive:%Y-%m-%d %H:%M:%S}' "
        f"AND ingested < TIMESTAMP '{t_end.replace(tzinfo=None):%Y-%m-%d %H:%M:%S}'"
    )
    sums = "sum(n_rows), sum(n_unique), sum(n_dup_resend), sum(n_dup_cross_id)"
    truth = con.sql(f"SELECT {sums} FROM '{(backfill_dir / 'file_index.parquet').as_posix()}' WHERE {window}")
    got = con.sql(
        f"SELECT {sums} FROM '{state.require('file_index').as_posix()}' WHERE status = 'live' AND {window}"
    )
    want_row, got_row = truth.fetchone(), got.fetchone()
    assert want_row is not None and got_row is not None
    rel = abs(int(got_row[1]) - int(want_row[1])) / max(1, int(want_row[1]))
    report = {
        "backfill": [int(v or 0) for v in want_row],
        "replay": [int(v or 0) for v in got_row],
        "unique_relative_difference": rel,
        "tolerance": tolerance,
        "pass": rel <= tolerance,
        "slowest_run_seconds": round(slowest),
        "reread_bytes": reread,
    }
    click.echo(json.dumps(report, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


@cli.group(name="state")
def state_group() -> None:
    """Move the census state to and from the repository's 'state' release."""


@state_group.command(name="pull")
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@click.option("--repo", required=True)
def state_pull(state_dir: Path, repo: str) -> None:
    manifest = state_mod.pull(State(state_dir), repo)
    log.info("pulled generation %d", manifest.generation)


@state_group.command(name="push")
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@click.option("--repo", required=True)
@click.option("--init", is_flag=True, help="Create the release for the very first push.")
def state_push(state_dir: Path, repo: str, init: bool) -> None:
    state = State(state_dir)
    state_mod.push(state, repo, init=init)
    log.info("pushed generation %d", state.manifest.generation)


@cli.command(name="layers")
@click.option("--state", "state_dir", type=click.Path(path_type=Path), required=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
@click.option("--providers", is_flag=True, help="Also publish per-provider views (after the DCDB heads-up).")
@click.option("--lis-polygon", type=click.Path(path_type=Path, exists=True), help="Also build the LIS layer.")
@engine_options
def layers_cmd(
    state_dir: Path, out: Path, providers: bool, lis_polygon: Path | None, memory_limit: str, threads: int
) -> None:
    """Build the dashboard's published data from the state."""
    con = _connect({"memory_limit": memory_limit, "threads": threads})
    state = State(state_dir)
    meta = layers.build(con, state, out, providers=providers)
    if lis_polygon is not None:
        lis.build(con, state, lis_polygon, out / "lis")
    click.echo(json.dumps(meta))


@cli.group(name="lis")
def lis_group() -> None:
    """Regional analysis (Long Island Sound)."""


@lis_group.command(name="extract")
@click.option(
    "--stage", "stage_root", type=click.Path(path_type=Path), required=True, help="Backfill stage/."
)
@click.option("--polygon", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--out", type=click.Path(path_type=Path), required=True)
@click.option("--res", default=10, show_default=True)
@engine_options
def lis_extract(
    stage_root: Path, polygon: Path, out: Path, res: int, memory_limit: str, threads: int
) -> None:
    """Every original sounding in the region with copy counts, plus a fine H3 layer (garnet)."""
    con = _connect({"memory_limit": memory_limit, "threads": threads})
    log.info("extracted %d unique soundings", lis.extract(con, stage_root, polygon, out, res=res))
