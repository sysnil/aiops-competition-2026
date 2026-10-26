"""Materialize Final answers already committed to SQLite without changing rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from app.final_store import archive_final_answer


def canonical_hash(answer: dict) -> str:
    return hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def backfill(database: Path, *, apply: bool) -> dict:
    connection = sqlite3.connect(f"file:{quote(str(database.resolve()), safe='/')}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT submission_id,answer_json,answer_sha256 FROM final_submissions ORDER BY received_at,submission_id"
        ).fetchall()
    finally:
        connection.close()
    parsed = []
    for submission_id, raw, digest in rows:
        answer = json.loads(raw)
        if not isinstance(answer, dict) or canonical_hash(answer) != digest:
            raise ValueError(f"database answer hash mismatch: {submission_id}")
        parsed.append((submission_id, digest, answer))
    root = database.parent / "final-answers"
    missing = [submission_id for submission_id, _, _ in parsed if not (root / f"{submission_id}.json").exists()]
    if apply:
        for submission_id, digest, answer in parsed:
            archive_final_answer(root, submission_id, digest, answer)
    return {"database_rows": len(rows), "files_missing_before": len(missing),
            "files_verified_after": len(parsed) if apply else len(rows) - len(missing),
            "applied": apply}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("/var/lib/aiops-grading/submissions.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(backfill(args.database, apply=args.apply), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
