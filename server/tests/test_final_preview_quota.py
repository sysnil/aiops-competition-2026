from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.final_api import preview_submission_limit
from app.final_store import FinalStore
from app.storage import StorageError


def test_preview_does_not_consume_official_submission_or_summary(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "tasks": [{"task_id": "FT-01", "case_id": "SF-006", "kind": "FT", "max_submissions": 1}],
    }), encoding="utf-8")
    store = FinalStore(tmp_path / "grading.db", catalog)
    answer = {"case_id": "SF-006"}
    digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    preview = store.create_submission("FT-01", "TEAM-01", "preview-key", digest, answer, preview=True, preview_submission_limit=3)
    store.set_grade(preview["submission_id"], {"status": "PREVIEW_COMPLETED", "score": 7})
    assert store.qualification_summary("TEAM-01", grading_mode="OFFICIAL")["total_score"] == 0
    assert store.qualification_summary("TEAM-01", grading_mode="PREVIEW")["total_score"] == 23.33
    second_preview = store.create_submission("FT-01", "TEAM-01", "preview-key-2", digest, answer, preview=True, preview_submission_limit=3)
    store.set_grade(second_preview["submission_id"], {"status": "PREVIEW_COMPLETED", "score": 6})

    store.transition_task("FT-01", "OPEN", "test", duration_seconds=600)
    official = store.create_submission("FT-01", "TEAM-01", "official-key", digest, answer, preview=False)
    assert official["grading_mode"] == "OFFICIAL"
    assert store.qualification_summary("TEAM-01", grading_mode="OFFICIAL")["tasks"][0]["score"] is None
    store.set_grade(official["submission_id"], {"status": "COMPLETED", "score": 8.5})
    official_summary = store.qualification_summary("TEAM-01", grading_mode="OFFICIAL")
    assert official_summary["total_score"] == 28.33
    assert official_summary["tasks"][0]["score"] == 85
    assert official_summary["tasks"][0]["raw_score"] == 8.5
    assert official_summary["scored_count"] == 1
    assert store.qualification_summary("TEAM-01", grading_mode="PREVIEW")["total_score"] == 20


def test_official_zero_score_is_counted_as_completed(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "tasks": [{"task_id": "FT-01", "case_id": "SF-006", "kind": "FT", "max_submissions": 1}],
    }), encoding="utf-8")
    store = FinalStore(tmp_path / "grading.db", catalog)
    answer = {"case_id": "SF-006"}
    digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store.transition_task("FT-01", "OPEN", "test", duration_seconds=600)
    official = store.create_submission("FT-01", "TEAM-01", "official-zero", digest, answer, preview=False)
    store.set_grade(official["submission_id"], {"status": "COMPLETED", "score": 0})

    summary = store.qualification_summary("TEAM-01", grading_mode="OFFICIAL")
    assert summary["total_score"] == 0
    assert summary["tasks"][0]["score"] == 0


def test_qualification_is_mean_of_three_displayed_task_scores(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "tasks": [{"task_id": f"FT-0{index}", "case_id": f"SF-00{index}",
                   "kind": "FT", "max_submissions": 1} for index in range(1, 4)],
    }), encoding="utf-8")
    store = FinalStore(tmp_path / "grading.db", catalog)
    for index, raw_score in enumerate((1, 2, 3), start=1):
        task_id = f"FT-0{index}"
        answer = {"case_id": f"SF-00{index}"}
        digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = store.create_submission(task_id, "TEAM-01", f"preview-{index}", digest, answer, preview=True)
        store.set_grade(row["submission_id"], {"status": "PREVIEW_COMPLETED", "score": raw_score})

    summary = store.qualification_summary("TEAM-01", grading_mode="PREVIEW")
    assert [item["score"] for item in summary["tasks"]] == [10, 20, 30]
    assert summary["total_score"] == 20
    assert summary["max_score"] == 100
    assert summary["aggregation"] == "THREE_TASK_MEAN"


def test_organizer_preview_quota_is_bounded_and_does_not_change_team_quota(tmp_path):
    organizer = SimpleNamespace(is_organizer_test=True)
    participant = SimpleNamespace(is_organizer_test=False)
    assert preview_submission_limit(organizer, preview=True, scoped=False, scoped_limit=None) == 20
    assert preview_submission_limit(organizer, preview=False, scoped=False, scoped_limit=None) is None
    assert preview_submission_limit(participant, preview=True, scoped=False, scoped_limit=None) is None
    assert preview_submission_limit(participant, preview=True, scoped=True, scoped_limit=3) == 3

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "tasks": [{"task_id": "FT-01", "case_id": "SF-006", "kind": "FT", "max_submissions": 1}],
    }), encoding="utf-8")
    store = FinalStore(tmp_path / "grading.db", catalog)
    answer = {"case_id": "SF-006"}
    digest = hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for index in range(20):
        store.create_submission("FT-01", "ORGANIZER-TEST", f"organizer-{index}", digest, answer,
                                preview=True, preview_submission_limit=20)
    with pytest.raises(StorageError, match="submission limit reached"):
        store.create_submission("FT-01", "ORGANIZER-TEST", "organizer-over-limit", digest, answer,
                                preview=True, preview_submission_limit=20)
    store.create_submission("FT-01", "TEAM-01", "team-first", digest, answer, preview=True)
    with pytest.raises(StorageError, match="submission limit reached"):
        store.create_submission("FT-01", "TEAM-01", "team-second", digest, answer, preview=True)
