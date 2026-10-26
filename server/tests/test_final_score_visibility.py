from __future__ import annotations

from app.final_api import visible_grade, visible_round_result


def test_active_round_exposes_only_own_numeric_score():
    grade = {
        "status": "COMPLETED", "score": 7.35,
        "breakdown": {"gt_events": 4, "event_scores": [{"entity_id": "secret"}]},
        "grader_version": "private-v1", "review_status": "APPROVED",
    }
    assert visible_grade(grade, show_details=False) == {"status": "COMPLETED", "score": 7.35, "display_score": 73.5, "display_max_score": 100}
    assert visible_grade({**grade, "score": 0}, show_details=False) == {"status": "COMPLETED", "score": 0, "display_score": 0, "display_max_score": 100}


def test_unscored_or_invalid_grade_does_not_create_a_number():
    assert visible_grade({"status": "PENDING_GT_APPROVAL"}, show_details=False) is None
    assert visible_grade({"status": "REJECTED", "score": 0}, show_details=False) is None
    assert visible_grade({"status": "COMPLETED", "score": float("nan")}, show_details=False) is None
    assert visible_grade({"status": "COMPLETED", "score": True}, show_details=False) is None
    assert visible_grade({"status": "COMPLETED", "score": 10.1}, show_details=False) is None


def test_hybrid_preview_still_redacts_gt_event_count():
    grade = {
        "status": "PREVIEW_COMPLETED", "score": 1.2,
        "grading_mode": "HYBRID_INTERNAL_PREVIEW",
        "breakdown": {"gt_events": 4, "matched_events": 2},
    }
    visible = visible_grade(grade, show_details=True)
    assert visible is not None
    assert visible["breakdown"] == {"matched_events": 2}
    assert visible["display_score"] == 12
    assert visible["display_max_score"] == 100


def test_pk_result_adds_hundred_point_display_without_changing_decision_score():
    result = {
        "rule": "ACCURACY_THEN_SERVER_RECEIPT_V1", "winner": "TEAM-01",
        "totals": {"TEAM-01": 2.5, "TEAM-02": 1.25},
        "entries": {"TEAM-01": {"score": 2.5}, "TEAM-02": {"score": 1.25}},
    }
    visible = visible_round_result(result)
    assert visible["totals"] == result["totals"]
    assert visible["display_totals"] == {"TEAM-01": 25, "TEAM-02": 12.5}
    assert visible["display_max_score"] == 100
    assert visible["entries"]["TEAM-01"]["display_score"] == 25
    assert result["entries"]["TEAM-01"] == {"score": 2.5}
    assert visible_round_result({"rule": "OLDER_RULE", "totals": {"TEAM-01": 80}}) == {
        "rule": "OLDER_RULE", "totals": {"TEAM-01": 80},
    }
