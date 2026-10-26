from __future__ import annotations

import hashlib
import json

import pytest

from app.final_store import FinalStore, archive_final_answer
from scripts.backfill_final_answers import backfill


def digest(answer: dict) -> str:
    return hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_final_answer_is_file_archived_and_idempotent(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "tasks": [{"task_id": "FT-01", "case_id": "SF-006", "kind": "FT", "max_submissions": 1}],
    }))
    database = tmp_path / "submissions.sqlite3"
    store = FinalStore(database, catalog)
    answer = {"case_id": "SF-006", "summary": "真实中文答案", "anomalies": []}
    row = store.create_submission("FT-01", "TEAM-01", "stable-idempotency-key", digest(answer), answer, preview=True)
    path = database.parent / "final-answers" / f"{row['submission_id']}.json"
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o640
    assert digest(json.loads(path.read_text())) == row["answer_sha256"]
    assert store.create_submission("FT-01", "TEAM-01", "stable-idempotency-key", digest(answer), answer, preview=True)["submission_id"] == row["submission_id"]

    path.unlink()
    assert backfill(database, apply=False)["files_missing_before"] == 1
    assert backfill(database, apply=True) == {"database_rows": 1, "files_missing_before": 1,
                                              "files_verified_after": 1, "applied": True}
    assert digest(json.loads(path.read_text())) == row["answer_sha256"]


def test_archive_refuses_mismatch_without_overwrite(tmp_path):
    answer = {"case_id": "SF-006", "summary": "original"}
    path = archive_final_answer(tmp_path, "FSUB-EXAMPLE001", digest(answer), answer)
    path.write_text('{"case_id":"SF-006","summary":"changed"}')
    with pytest.raises(ValueError, match="differs"):
        archive_final_answer(tmp_path, "FSUB-EXAMPLE001", digest(answer), answer)
    assert "changed" in path.read_text()


def test_archive_rejects_forged_digest_before_file_creation(tmp_path):
    with pytest.raises(ValueError, match="digest mismatch"):
        archive_final_answer(tmp_path, "FSUB-EXAMPLE002", "0" * 64, {"case_id": "SF-006"})
    assert not (tmp_path / "FSUB-EXAMPLE002.json").exists()
