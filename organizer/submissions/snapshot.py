"""Publish an atomic, standalone scoring snapshot for the read-only viewer.

The live database uses WAL. Never copy its main file or open it immutable:
both can omit committed submissions still in WAL.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


def publish(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.absolute()
    if source == destination.resolve():
        raise ValueError("snapshot must differ from the live database")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite3", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        uri = f"file:{quote(str(source), safe='/')}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as reader:
            reader.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(temporary)) as writer:
                reader.backup(writer, pages=256, sleep=0.01)
                # A standalone DELETE-mode file needs no writable WAL sidecars.
                if writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise RuntimeError("snapshot is not standalone")
                if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("snapshot integrity check failed")
                tables = {row[0] for row in writer.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"submissions", "final_submissions"}.issubset(tables):
                    raise RuntimeError("snapshot is missing scoring tables")
        os.chmod(temporary, 0o600)
        # Mark the acquisition time even when no source rows have changed.
        os.utime(temporary, None)
        with temporary.open("rb") as output:
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/var/lib/aiops-grading/submissions.sqlite3"))
    parser.add_argument("--destination", type=Path, default=Path("/var/lib/aiops-submission-snapshot/submissions.sqlite3"))
    args = parser.parse_args()
    publish(args.source, args.destination)
