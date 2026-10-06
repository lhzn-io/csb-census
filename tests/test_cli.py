"""CLI wiring: every command parses, and ``layers`` runs end to end on a seeded state."""

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from click.testing import CliRunner

from csb_census.cli import cli
from tests.synthetic import seeded, step

COMMANDS = [
    ["backfill"],
    ["rank"],
    ["seed"],
    ["incremental"],
    ["reconcile"],
    ["replay"],
    ["state", "pull"],
    ["state", "push"],
    ["layers"],
    ["lis", "extract"],
]


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_help(command: list[str]) -> None:
    result = CliRunner().invoke(cli, [*command, "--help"])
    assert result.exit_code == 0, result.output


def test_layers_command(con: duckdb.DuckDBPyConnection, archive: Path, tmp_path: Path) -> None:
    state, source = seeded(con, archive, tmp_path)
    step(con, state, source, datetime(2026, 9, 3, 12, tzinfo=UTC), tmp_path)
    out = tmp_path / "site"
    result = CliRunner().invoke(
        cli,
        ["layers", "--state", str(state.root), "--out", str(out), "--threads", "2", "--memory-limit", "1GB"],
    )
    assert result.exit_code == 0, result.output
    meta = json.loads((out / "meta.json").read_text())
    assert meta["unique"] == 10 and meta["provider_views"] is False
