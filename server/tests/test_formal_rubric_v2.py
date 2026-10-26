from __future__ import annotations

import hashlib
import json

from grading.private_grader import service
from grading.private_grader.final_scoring import score_final


GT_HASH = "918c90331f260075987ca16164088c76fb2d716d73fe0ee5e631c2dba65d331c"


def test_uniform_formal_policy_is_pinned_and_fail_closed(tmp_path, monkeypatch):
    policy = {
        "version": "final-balanced-evidence-v2.0", "status": "ACTIVE_FORMAL",
        "applies_to": "FUTURE_OFFICIAL_FINAL_SUBMISSIONS_ALL_TEAMS",
        "approved_gt_manifest_sha256": GT_HASH, "max_points": 10.0,
        "strict_positive_credit": True, "type": 40.0, "entity": 20.0,
        "time": 20.0, "evidence": 20.0, "time_tolerance_seconds": 300,
        "false_positive_penalty": 2.0, "duplicate_penalty": 2.0,
        "empty_answer_score": 0.0,
    }
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(service, "FINAL_GT_MANIFEST_SHA256", GT_HASH)
    monkeypatch.setattr(service, "FINAL_RUBRIC_PATH", path)
    monkeypatch.setattr(service, "FINAL_RUBRIC_SHA256", digest)
    result = service.load_formal_final_rubric()
    assert result is not None
    assert result["time_tolerance_seconds"] == 300
    assert result["false_positive_penalty"] == 2.0
    assert result["strict_positive_credit"] is True
    path.write_text(json.dumps({**policy, "false_positive_penalty": 0.0}))
    assert service.load_formal_final_rubric() is None
    monkeypatch.setattr(service, "FINAL_RUBRIC_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    assert service.load_formal_final_rubric() is None


def test_empty_answer_remains_zero_under_relaxed_rule():
    answer = {"case_id": "SF-004", "anomalies": []}
    gt = {"case_id": "SF-004", "status": "APPROVED", "events": [{
        "anomaly_type": "TRACE_LATENCY_INCREASE", "accepted_anomaly_types": [],
        "entity_type": "Service", "entity_id": "frontend",
        "observable_start_time": "2026-10-20T00:00:00Z",
        "observable_end_time": "2026-10-20T00:01:00Z",
    }]}
    rubric = {"version": "final-balanced-evidence-v2.0", "strict_positive_credit": True,
              "time_tolerance_seconds": 300, "false_positive_penalty": 2.0,
              "duplicate_penalty": 2.0}
    result = score_final(answer, gt, {"valid": True, "checks": []}, rubric=rubric)
    assert result["score"] == 0
    assert result["rubric_version"] == "final-balanced-evidence-v2.0"
