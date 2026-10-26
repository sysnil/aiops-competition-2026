from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.accuracy_speed import decide_accuracy_speed_round
from app.final_store import FinalStore
from app.storage import StorageError


OPENED = "2026-10-19T09:00:00+00:00"
CLOSED = "2026-10-19T09:01:00+00:00"


def submission(team: str, score: float, milliseconds: int, status: str = "PREVIEW_COMPLETED") -> dict:
    return {
        "submission_id": team,
        "status": status,
        "received_at": (datetime.fromisoformat(OPENED) + timedelta(milliseconds=milliseconds)).isoformat(),
        "grade_json": json.dumps({"status": status, "score": score}),
    }


def test_score_comes_before_speed_and_does_not_use_base_or_judges() -> None:
    result = decide_accuracy_speed_round(
        ("A", "B"), {"A": submission("A", 8, 900), "B": submission("B", 7, 100)}, OPENED, CLOSED,
    )
    assert result["winner"] == "A"
    assert result["totals"] == {"A": 8, "B": 7}
    assert result["decision_basis"] == "ANOMALY_SCORE"


def test_equal_scores_use_server_receipt_even_at_zero() -> None:
    result = decide_accuracy_speed_round(
        ("A", "B"), {"A": submission("A", 0, 300), "B": submission("B", 0, 200)}, OPENED, CLOSED,
    )
    assert result["winner"] == "B"
    assert result["entries"]["B"]["elapsed_seconds"] == 0.2
    assert result["decision_basis"] == "EARLIEST_VALID_SERVER_RECEIPT"


def test_failed_or_missing_submission_does_not_win_a_zero_score_tie() -> None:
    result = decide_accuracy_speed_round(
        ("A", "B"), {"A": submission("A", 0, 100, "REJECTED"), "B": submission("B", 0, 300)}, OPENED, CLOSED,
    )
    assert result["winner"] == "B"
    assert result["entries"]["A"]["received_at"] is None
    assert decide_accuracy_speed_round(("A", "B"), {"A": None, "B": None}, OPENED, CLOSED)["winner"] is None


def test_pending_grading_and_indistinguishable_receipts_fail_closed() -> None:
    with pytest.raises(ValueError, match="not terminal"):
        decide_accuracy_speed_round(("A", "B"), {"A": submission("A", 0, 100, "GRADE_PENDING"), "B": None}, OPENED, CLOSED)
    assert decide_accuracy_speed_round(
        ("A", "B"), {"A": submission("A", 0, 100), "B": submission("B", 0, 100)}, OPENED, CLOSED,
    )["winner"] is None


def test_scored_submission_outside_open_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside round window"):
        decide_accuracy_speed_round(
            ("A", "B"), {"A": submission("A", 10, 60_000), "B": None}, OPENED, CLOSED,
        )


def test_accuracy_speed_batch_skips_judge_and_preserves_round_audit(tmp_path) -> None:
    catalog = {
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "preview_scoring": {
            "version": "stage3-accuracy-speed-candidate-1.0",
            "round_winner_rule": "ACCURACY_THEN_SERVER_RECEIPT_V1",
            "internal_preview_only": True,
        },
        "tasks": [{"task_id": "PK-R01", "case_id": "SF-004", "kind": "PK", "max_submissions": 1, "simulation_only": True}],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    store = FinalStore(tmp_path / "new-batch.sqlite3", catalog_path)
    store.configure_round("PK-R01", "A", "B", "test")
    store.transition_round("PK-R01", "RUNNING", "test", 60)
    rows = {}
    for team in ("A", "B"):
        answer = {"case_id": "SF-004"}
        digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        rows[team] = store.create_submission(
            "PK-R01", team, team, digest, answer, preview=True,
            grading_mode="INTERNAL_PREVIEW",
        )
    store.set_grade(rows["A"]["submission_id"], {"status": "PREVIEW_COMPLETED", "score": 3})
    store.set_grade(rows["B"]["submission_id"], {"status": "PREVIEW_COMPLETED", "score": 2})
    store.transition_round("PK-R01", "SUBMISSION_LOCKED", "test")
    with pytest.raises(StorageError) as judged:
        store.judge("PK-R01", "A", "J", 50, "unused", "test")
    assert judged.value.code == "JUDGING_DISABLED"
    with pytest.raises(StorageError) as transitioned:
        store.transition_round("PK-R01", "JUDGING", "test")
    assert transitioned.value.code == "JUDGING_DISABLED"
    assert store.confirm_round("PK-R01", "test")["winner_team_id"] == "A"
    audit = store.confirmed_result("PK-R01")
    assert audit["rule"] == "ACCURACY_THEN_SERVER_RECEIPT_V1"
    assert audit["totals"] == {"A": 3, "B": 2}
    assert audit["entries"]["A"]["received_at"] == rows["A"]["received_at"]
