"""Versioned census state: a directory of Parquet tables plus a manifest, mirrored to a GitHub release.

Every generation writes its tables under new names (``<table>.g<N>.parquet``) and then replaces
``manifest.json`` atomically, so a crash at any point leaves the previous generation intact.
The remote copy (release tag ``state``) follows the same rule: tables first, manifest last, and a
push is refused unless the remote is still at the generation this one was built on.

Tables (see docs/src/methodology.md for their meaning):

    file_index     one row per (published file, UNIQUE_ID): identity, fingerprint, counts, status
    file_months    counts per (file, UNIQUE_ID, collection month): time series and exact removals
    cells_base     counts per (provider, collection month, H3 r9) as of the last rebaseline
    cells_delta    signed additions to cells_base from incremental runs and removals
    recent_cells   per-file cell and day counts for recently ingested files (exact subtraction on removal)
    vdays_base     counts per (provider, platform, collection day, H3 r8) as of the last rebaseline
    vdays_delta    signed additions to vdays_base
    pending        keys seen in a listing but not yet processed
    queue          (provider, collection month) pairs needing an exact recount on garnet
    runs           one row per incremental run or reconcile that changed the state
"""

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
TABLES = (
    "file_index",
    "file_months",
    "cells_base",
    "cells_delta",
    "recent_cells",
    "vdays_base",
    "vdays_delta",
    "uw_base",
    "uw_delta",
    "pending",
    "queue",
    "runs",
)
KEEP_GENERATIONS = 3
RELEASE_TAG = "state"
MANIFEST = "manifest.json"

Gh = Callable[[list[str]], str]


class GenerationConflict(RuntimeError):
    """The remote state moved on since this state was pulled."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Asset:
    name: str
    sha256: str
    bytes: int


@dataclass
class Manifest:
    generation: int = 0
    parent: int | None = None
    schema_version: int = SCHEMA_VERSION
    git_sha: str = ""
    max_ingested: str | None = None  # ISO timestamp of the newest ingested file
    last_listed_day: str | None = None
    assets: dict[str, Asset] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        raw = json.loads(text)
        raw["assets"] = {k: Asset(**v) for k, v in raw.get("assets", {}).items()}
        manifest = cls(**raw)
        if manifest.schema_version != SCHEMA_VERSION:
            raise ValueError(f"state schema {manifest.schema_version}, code expects {SCHEMA_VERSION}")
        return manifest


class State:
    """A local state directory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        path = root / MANIFEST
        self.manifest = Manifest.from_json(path.read_text()) if path.exists() else Manifest()

    def path(self, table: str) -> Path | None:
        asset = self.manifest.assets.get(table)
        return self.root / asset.name if asset else None

    def require(self, table: str) -> Path:
        path = self.path(table)
        if path is None:
            raise FileNotFoundError(f"state has no {table!r} table (generation {self.manifest.generation})")
        return path

    def verify(self) -> None:
        for table, asset in self.manifest.assets.items():
            path = self.root / asset.name
            if not path.exists() or sha256(path) != asset.sha256:
                raise ValueError(f"state table {table!r} is missing or corrupt: {path}")

    def commit(self, tables: Mapping[str, Path], **fields: str | None) -> Manifest:
        """Adopt new table files as the next generation. Unlisted tables carry over unchanged."""
        unknown = set(tables) - set(TABLES)
        if unknown:
            raise ValueError(f"unknown state tables: {sorted(unknown)}")
        current = self.manifest
        nxt = Manifest(
            generation=current.generation + 1,
            parent=current.generation,
            git_sha=os.environ.get("GITHUB_SHA", current.git_sha),
            max_ingested=current.max_ingested,
            last_listed_day=current.last_listed_day,
            assets=dict(current.assets),
        )
        for key, value in fields.items():
            if key not in ("max_ingested", "last_listed_day", "git_sha"):
                raise ValueError(f"unknown manifest field {key!r}")
            setattr(nxt, key, value)
        for table, src in tables.items():
            name = f"{table}.g{nxt.generation}.parquet"
            dst = self.root / name
            shutil.copyfile(src, dst)
            nxt.assets[table] = Asset(name, sha256(dst), dst.stat().st_size)
        tmp = self.root / f"{MANIFEST}.tmp"
        tmp.write_text(nxt.to_json())
        (self.root / f"manifest.g{nxt.generation}.json").write_text(nxt.to_json())
        os.replace(tmp, self.root / MANIFEST)  # the commit point
        self.manifest = nxt
        self._prune()
        return nxt

    def _prune(self) -> None:
        keep = {a.name for a in self.manifest.assets.values()}
        floor = self.manifest.generation - KEEP_GENERATIONS + 1
        for path in self.root.glob("*.g*.*"):
            gen = _generation_of(path.name)
            if gen is not None and gen < floor and path.name not in keep:
                path.unlink()


def _generation_of(name: str) -> int | None:
    for part in name.split("."):
        if part.startswith("g") and part[1:].isdigit():
            return int(part[1:])
    return None


def run_gh(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def remote_manifest(repo: str, gh: Gh = run_gh, *, scratch: Path) -> Manifest | None:
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        gh(["release", "download", RELEASE_TAG, "--repo", repo, "--pattern", MANIFEST,
            "--dir", str(scratch), "--clobber"])  # fmt: skip
    except subprocess.CalledProcessError:
        return None  # no release or no manifest yet
    return Manifest.from_json((scratch / MANIFEST).read_text())


def pull(state: State, repo: str, gh: Gh = run_gh) -> Manifest:
    """Make the local directory match the remote generation (downloading only changed tables)."""
    remote = remote_manifest(repo, gh, scratch=state.root / ".remote")
    if remote is None:
        raise FileNotFoundError(f"{repo} has no {RELEASE_TAG!r} release manifest; seed with push --init")
    for asset in remote.assets.values():
        local = state.root / asset.name
        if local.exists() and sha256(local) == asset.sha256:
            continue
        gh(["release", "download", RELEASE_TAG, "--repo", repo, "--pattern", asset.name,
            "--dir", str(state.root), "--clobber"])  # fmt: skip
    (state.root / MANIFEST).write_text(remote.to_json())
    state.manifest = remote
    state.verify()
    return remote


def push(state: State, repo: str, gh: Gh = run_gh, *, init: bool = False) -> None:
    """Publish the local generation: tables first, manifest last, refusing a stale parent."""
    remote = remote_manifest(repo, gh, scratch=state.root / ".remote")
    if remote is None:
        if not init:
            raise FileNotFoundError(f"{repo} has no state yet; use init=True for the first push")
        notes = "Machine-written census state. See docs/src/methodology.md."
        gh(
            [
                "release",
                "create",
                RELEASE_TAG,
                "--repo",
                repo,
                "--title",
                "Census state",
                "--notes",
                notes,
                "--latest=false",
            ]
        )
    elif remote == state.manifest:
        return  # this exact generation is already published
    elif remote.generation != state.manifest.parent:
        raise GenerationConflict(
            f"remote is at generation {remote.generation}, local {state.manifest.generation} "
            f"was built on {state.manifest.parent}; pull and rerun"
        )
    remote_names = {a.name for a in remote.assets.values()} if remote else set()
    for asset in state.manifest.assets.values():
        if asset.name not in remote_names:
            gh(["release", "upload", RELEASE_TAG, str(state.root / asset.name), "--repo", repo, "--clobber"])
    gh(["release", "upload", RELEASE_TAG, str(state.root / MANIFEST), "--repo", repo, "--clobber"])
    # Prune old generations on the remote, keeping what the last few manifests reference.
    listed = gh(
        ["release", "view", RELEASE_TAG, "--repo", repo, "--json", "assets", "--jq", ".assets[].name"]
    )
    keep = {a.name for a in state.manifest.assets.values()} | {MANIFEST}
    floor = state.manifest.generation - KEEP_GENERATIONS + 1
    for name in listed.split():
        gen = _generation_of(name)
        if name not in keep and gen is not None and gen < floor:
            gh(["release", "delete-asset", RELEASE_TAG, name, "--repo", repo, "--yes"])
