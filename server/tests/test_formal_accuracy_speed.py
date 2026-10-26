from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest

from app.final_store import FinalStore, parse_time
from app.storage import StorageError


def make_store(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "preview_scoring": {
            "round_winner_rule": "ACCURACY_THEN_SERVER_RECEIPT_V1",
            "internal_preview_only": False,
        },
        "tasks": [{"task_id": "PK-R01", "case_id": "SF-004", "kind": "PK",
                   "max_submissions": 1}],
    }), encoding="utf-8")
    store = FinalStore(tmp_path / "grades.sqlite3", catalog)
    store.configure_round("PK-R01", "TEAM-A", "TEAM-B", "test")
    store.transition_round("PK-R01", "RUNNING", "test", duration_seconds=600)
    return store


def submit(store, team, mode, score=None):
    answer = {"case_id": "SF-004"}
    digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    row = store.create_submission("PK-R01", team, f"{team}-{mode}", digest, answer,
                                  preview=mode != "OFFICIAL", grading_mode=mode)
    if score is not None:
        store.set_grade(row["submission_id"], {
            "status": "COMPLETED" if mode == "OFFICIAL" else "PREVIEW_COMPLETED",
            "score": score, "grading_mode": mode,
        })
    return row


def receipt_times(store, *, a_seconds=10, b_seconds=20):
    opened = parse_time(store.round("PK-R01")["opens_at"])
    with store._connect() as db:
        for team, seconds in (("TEAM-A", a_seconds), ("TEAM-B", b_seconds)):
            db.execute("UPDATE final_submissions SET received_at=? WHERE team_id=? AND grading_mode='OFFICIAL'",
                       ((opened + timedelta(seconds=seconds)).isoformat(), team))


def test_formal_round_uses_score_then_server_receipt_without_judges(tmp_path):
    store = make_store(tmp_path)
    submit(store, "TEAM-A", "OFFICIAL", 7.0)
    submit(store, "TEAM-B", "OFFICIAL", 8.0)
    receipt_times(store, a_seconds=10, b_seconds=20)
    store.transition_round("PK-R01", "SUBMISSION_LOCKED", "test")
    with pytest.raises(StorageError) as error:
        store.transition_round("PK-R01", "JUDGING", "test")
    assert error.value.code == "JUDGING_DISABLED"
    result = store.confirm_round("PK-R01", "test")
    assert result["winner_team_id"] == "TEAM-B"
    assert store.confirmed_result("PK-R01")["decision_basis"] == "ANOMALY_SCORE"


def test_equal_score_uses_earlier_valid_receipt(tmp_path):
    store = make_store(tmp_path)
    submit(store, "TEAM-A", "OFFICIAL", 7.0)
    submit(store, "TEAM-B", "OFFICIAL", 7.0)
    receipt_times(store, a_seconds=20, b_seconds=10)
    store.transition_round("PK-R01", "SUBMISSION_LOCKED", "test")
    assert store.confirm_round("PK-R01", "test")["winner_team_id"] == "TEAM-B"
    assert store.confirmed_result("PK-R01")["decision_basis"] == "EARLIEST_VALID_SERVER_RECEIPT"


def test_preview_result_cannot_override_official_round(tmp_path):
    store = make_store(tmp_path)
    submit(store, "TEAM-A", "PREVIEW", 10.0)
    submit(store, "TEAM-B", "OFFICIAL", 3.0)
    receipt_times(store)
    store.transition_round("PK-R01", "SUBMISSION_LOCKED", "test")
    assert store.confirm_round("PK-R01", "test")["winner_team_id"] == "TEAM-B"
