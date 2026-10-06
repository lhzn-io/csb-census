"""State generations and the release mirror, with ``gh`` faked by a directory of assets."""

import shutil
import subprocess
from pathlib import Path

import pytest

from csb_census.state import MANIFEST, GenerationConflict, State, pull, push


class FakeRelease:
    """Implements the handful of ``gh release`` calls state.py makes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.exists = False
        self.uploads: list[str] = []

    def __call__(self, args: list[str]) -> str:
        verb = args[1]
        if verb == "create":
            self.exists = True
            self.root.mkdir(parents=True, exist_ok=True)
            return ""
        if not self.exists:
            raise subprocess.CalledProcessError(1, args)
        if verb == "download":
            pattern, dest = args[args.index("--pattern") + 1], Path(args[args.index("--dir") + 1])
            src = self.root / pattern
            if not src.exists():
                raise subprocess.CalledProcessError(1, args)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / pattern)
            return ""
        if verb == "upload":
            src = Path(args[3])
            shutil.copyfile(src, self.root / src.name)
            self.uploads.append(src.name)
            return ""
        if verb == "view":
            return "\n".join(sorted(p.name for p in self.root.iterdir()))
        if verb == "delete-asset":
            (self.root / args[3]).unlink()
            return ""
        raise AssertionError(f"unexpected gh call {args}")


def table(tmp: Path, name: str, content: bytes) -> Path:
    path = tmp / f"{name}.src"
    path.write_bytes(content)
    return path


def test_commit_carries_unchanged_tables_and_prunes(tmp_path: Path) -> None:
    state = State(tmp_path / "s")
    state.commit({"file_index": table(tmp_path, "a", b"1"), "pending": table(tmp_path, "b", b"2")})
    for i in range(4):
        state.commit({"file_index": table(tmp_path, "a", str(i).encode())})
    m = state.manifest
    assert m.generation == 5 and m.parent == 4
    assert m.assets["pending"].name == "pending.g1.parquet"  # carried over, never pruned
    assert sorted(p.name for p in state.root.glob("file_index.g*")) == [
        "file_index.g3.parquet",
        "file_index.g4.parquet",
        "file_index.g5.parquet",
    ]
    state.verify()
    assert State(state.root).manifest == m


def test_push_pull_round_trip_and_generation_conflict(tmp_path: Path) -> None:
    gh = FakeRelease(tmp_path / "release")
    writer = State(tmp_path / "w")
    writer.commit({"file_index": table(tmp_path, "a", b"one")})
    with pytest.raises(FileNotFoundError):
        push(writer, "o/r", gh)
    push(writer, "o/r", gh, init=True)
    assert gh.uploads[-1] == MANIFEST  # manifest is always last

    reader = State(tmp_path / "r")
    pull(reader, "o/r", gh)
    assert reader.manifest.generation == 1
    assert reader.require("file_index").read_bytes() == b"one"

    # Two writers build on generation 1; the second push must be refused.
    reader.commit({"file_index": table(tmp_path, "a", b"two")})
    writer.commit({"file_index": table(tmp_path, "a", b"other")})
    push(reader, "o/r", gh)
    with pytest.raises(GenerationConflict):
        push(writer, "o/r", gh)


def test_pull_rejects_corrupt_asset(tmp_path: Path) -> None:
    gh = FakeRelease(tmp_path / "release")
    writer = State(tmp_path / "w")
    writer.commit({"file_index": table(tmp_path, "a", b"good")})
    push(writer, "o/r", gh, init=True)
    (gh.root / "file_index.g1.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="corrupt"):
        pull(State(tmp_path / "r"), "o/r", gh)
