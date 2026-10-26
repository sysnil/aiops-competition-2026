from __future__ import annotations

import json
import hashlib

from app.final_store import FinalStore
from grading.private_grader.final_scoring import normalize_draft


def answer_hash(answer: dict) -> str:
    return hashlib.sha256(json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_internal_preview_uses_merged_events() -> None:
    draft = {
        "status": "DRAFT_PENDING_HUMAN_REVIEW",
        "candidate_count": 3,
        "merged_event_count": 1,
        "events": [
            {"anomaly_id": f"a-{i}", "anomaly_type": "LATENCY_INCREASE", "entity_type": "Service", "entity_id": "frontend", "start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-01T00:01:00Z"}
            for i in range(3)
        ],
        "merged_events": [{
            "event_id": "m-1",
            "anomaly_types": ["LATENCY_INCREASE", "REQUEST_ERROR_INCREASE"],
            "entity_type": "Service",
            "entity_id": "frontend",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-01T00:02:00Z",
        }],
    }
    gt = normalize_draft(draft, "SF-001", prefer_merged=True, version="stage2")
    assert gt["event_source"] == "merged_events"
    assert gt["candidate_count"] == 3
    assert gt["merged_event_count"] == 1
    assert len(gt["events"]) == 1
    assert gt["events"][0]["accepted_anomaly_types"] == ["REQUEST_ERROR_INCREASE"]


def test_internal_preview_scores_are_isolated_and_reused_by_round(tmp_path) -> None:
    catalog = {
        "publication_state": "INTERNAL_DEVELOPMENT_ONLY",
        "preview_scoring": {
            "version": "stage2",
            "base_max": 30,
            "pk_auto_max": 40,
            "judge_max": 30,
            "internal_preview_only": True,
        },
        "tasks": [
            *[{"task_id": f"FT-0{i}", "case_id": f"SF-00{i}", "kind": "FT", "max_submissions": 1, "simulation_only": True} for i in range(1, 4)],
            {"task_id": "PK-R01", "case_id": "SF-004", "kind": "PK", "max_submissions": 1, "simulation_only": False},
        ],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    store = FinalStore(tmp_path / "stage2.sqlite3", catalog_path)

    for team, base in (("A", 7.0), ("B", 8.0)):
        for task in ("FT-01", "FT-02", "FT-03"):
            answer = {"case_id": store.task(task)["case_id"]}
            row = store.create_submission(
                task,
                team,
                f"{task}-{team}",
                answer_hash(answer),
                answer,
                preview=True,
                grading_mode="INTERNAL_PREVIEW",
            )
            store.set_grade(row["submission_id"], {"status": "PREVIEW_COMPLETED", "score": base, "grading_mode": "INTERNAL_PREVIEW"})
        summary = store.qualification_summary(team)
        assert summary["total_score"] == base * 10
        assert summary["max_score"] == 100
        assert summary["scored_count"] == 3
        assert [item["score"] for item in summary["tasks"]] == [base * 10] * 3

    store.configure_round("PK-R01", "A", "B", "test")
    store.transition_round("PK-R01", "RUNNING", "test", 60)
    for team, score in (("A", 4.0), ("B", 9.0)):
        answer = {"case_id": "SF-004"}
        row = store.create_submission(
            "PK-R01", team, f"pk-{team}", answer_hash(answer),
            answer, preview=True, grading_mode="INTERNAL_PREVIEW",
        )
        store.set_grade(row["submission_id"], {"status": "PREVIEW_COMPLETED", "score": score, "grading_mode": "INTERNAL_PREVIEW"})
    store.transition_round("PK-R01", "SUBMISSION_LOCKED", "test")
    store.transition_round("PK-R01", "JUDGING", "test")
    store.judge("PK-R01", "A", "J", 50, "test", "test")
    store.judge("PK-R01", "B", "J", 80, "test", "test")
    result = store.confirm_round("PK-R01", "test")
    assert result["winner_team_id"] == "B"
    persisted = store.confirmed_result("PK-R01")
    assert persisted and persisted["winner"] == "B"
    assert set(persisted["totals"]) == {"A", "B"}
