from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from grading.private_grader import service


def write_json(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_hybrid_loader_is_manifest_bound_and_preview_only(tmp_path, monkeypatch):
    case_id = "SF-004"
    snapshot = {
        "batch_id": "FINAL-STAGE3-HYBRID-GT",
        "classification": "HYBRID_INTERNAL_PREVIEW_ONLY",
        "status": "DRAFT_PREVIEW_ONLY",
        "case_id": case_id,
        "version": "test-hybrid-v1",
        "events": [{
            "gt_event_id": "EVT-0001", "anomaly_type": "TRACE_LATENCY_INCREASE",
            "accepted_anomaly_types": [], "entity_type": "SERVICE",
            "entity_id": "frontend", "observable_start_time": "2026-10-18T10:01:00Z",
            "observable_end_time": "2026-10-18T10:02:00Z",
            "accepted_entity_aliases": [], "source": "HUMAN_ACCEPTED",
        }],
    }
    snapshot_sha = write_json(tmp_path / f"{case_id}.json", snapshot)
    manifest_sha = write_json(tmp_path / "gt-freeze-manifest.json", {
        "batch_id": "FINAL-STAGE3-HYBRID-GT", "formal_gt_approved": False,
        "cases": [{"case_id": case_id, "snapshot_file": f"{case_id}.json", "snapshot_sha256": snapshot_sha}],
    })
    monkeypatch.setattr(service, "FINAL_HYBRID_ROOT", tmp_path)
    monkeypatch.setattr(service, "FINAL_HYBRID_MANIFEST_SHA256", manifest_sha)
    assert service.load_final_hybrid_gt(case_id) == snapshot
    answer = {"case_id": case_id, "task_type": "FINAL_ANOMALY_INVESTIGATION",
              "schema_version": "final-1.0", "anomalies": []}
    result = asyncio.run(service.grade(service.GradeRequest(
        submission_id="test-submission", case_id=case_id,
        answer=answer, evidence_validation={"valid": True, "checks": []},
        grading_mode="HYBRID_INTERNAL_PREVIEW",
    )))
    assert result["status"] == "PREVIEW_COMPLETED"
    assert result["score"] == 0
    assert result["review_status"] == "HYBRID_GT_INTERNAL_PREVIEW_ONLY"
    assert result["gt_version_identifier"] == "test-hybrid-v1"
    assert "frontend" not in json.dumps(result)
    (tmp_path / f"{case_id}.json").write_text("{}")
    assert service.load_final_hybrid_gt(case_id) is None


def test_stage2_mode_and_official_mode_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "FINAL_HYBRID_ROOT", tmp_path)
    monkeypatch.setattr(service, "FINAL_HYBRID_MANIFEST_SHA256", "0" * 64)
    assert service.load_final_hybrid_gt("INVALID") is None
    # The hybrid loader cannot read a plain Stage 2 draft without its own manifest.
    write_json(tmp_path / "SF-004.json", {"status": "DRAFT_PENDING_HUMAN_REVIEW"})
    assert service.load_final_hybrid_gt("SF-004") is None
