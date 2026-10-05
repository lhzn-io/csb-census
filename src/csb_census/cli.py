"""``csb-census`` command-line entry point."""

import logging
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import click

from csb_census import __version__, pipeline
from csb_census.inventory import day_prefix, download, list_objects

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
