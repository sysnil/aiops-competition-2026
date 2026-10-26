from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


TYPE_MAP = {
    "MEMORY_WORKING_SET_INCREASE": "MEMORY_USAGE_INCREASE",
    "EXPLICIT_ERROR_LOG_INCREASE": "LOG_ERROR_BURST",
    "TRACE_ERROR_BURST": "REQUEST_ERROR_INCREASE",
    "TRACE_LATENCY_INCREASE": "TRACE_LATENCY_INCREASE",
    "CALL_EDGE_CLIENT_ERROR_BURST": "CALL_EDGE_ERROR_INCREASE",
    "CALL_EDGE_CLIENT_LATENCY_INCREASE": "CALL_EDGE_LATENCY_INCREASE",
}
ENTITY_MAP = {"SERVICE": "Service", "POD": "Pod", "NODE": "Node", "CALL_EDGE": "CallEdge"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source: Path) -> dict[str, Any]:
    draft = json.loads(source.read_text(encoding="utf-8"))
    events = []
    for index, item in enumerate(draft.get("merged_events", []), start=1):
        mapped = []
        for value in item.get("anomaly_types", []):
            category = TYPE_MAP.get(value)
            if category and category not in mapped:
                mapped.append(category)
        if not mapped:
            mapped = ["OTHER_OBSERVABLE_ANOMALY"]
        modalities = ", ".join(item.get("modalities", [])) or "Public telemetry"
        events.append({
            "gt_event_id": f"SF001-PREVIEW-{index:03d}",
            "anomaly_type": mapped[0],
            "accepted_anomaly_types": mapped[1:],
            "entity_type": ENTITY_MAP.get(str(item.get("entity_type")), str(item.get("entity_type"))),
            "entity_id": str(item.get("entity_id")),
            "accepted_entity_aliases": [],
            "observable_start_time": item["start_time"],
            "observable_end_time": item["end_time"],
            "allow_service_level_partial": False,
            "evidence_rubric": (
                f"PREVIEW ONLY. FULL requires verified {modalities} records for the same entity and interval, "
                "with enough comparison or repeated observations to support the submitted observable phenomenon. "
                "PARTIAL is relevant but incomplete or weakly compared. NONE is unrelated, normal, wrong-entity, or wrong-window evidence."
            ),
            "review_status": "DRAFT",
            "gt_version": "sf001-anomaly-preview-1",
            "rubric_version": "semifinal-anomaly-rubric-preview-1",
        })
    return {
        "case_id": "SF-001",
        "task_type": "SEMIFINAL_ANOMALY_DETECTION",
        "classification": "DRAFT_PREVIEW_ONLY",
        "review_status": "DRAFT",
        "gt_version": "sf001-anomaly-preview-1",
        "rubric_version": "semifinal-anomaly-rubric-preview-1",
        "matching_rule_version": "entity-time-matching-2.0",
        "time_tolerance_seconds": 120,
        "source_draft_sha256": sha256(source),
        "warning": "UNAPPROVED PREVIEW GT. MUST NOT BE USED FOR FORMAL SCORES OR LEADERBOARDS.",
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build isolated SF-001 preview GT without modifying the source draft")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.chmod(0o640)
    print(f"wrote {len(payload['events'])} PREVIEW-only events to {args.output}")


if __name__ == "__main__":
    main()
