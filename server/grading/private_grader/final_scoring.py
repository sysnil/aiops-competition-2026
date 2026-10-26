from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .matching import deduplicate_events, entity_quality, pair_weight


DEFAULT_RUBRIC = {
    "version": "final-stage1-preview-1.1",
    "max_points": 10.0,
    "type": 40.0,
    "entity": 20.0,
    "time": 20.0,
    "evidence": 20.0,
    "false_positive_penalty": 5.0,
    "duplicate_penalty": 5.0,
    "time_tolerance_seconds": 120,
}


def _utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def _assignment(weights: list[list[int | None]]) -> list[tuple[int, int]]:
    """Maximum-weight one-to-one matching with explicit unmatched slots."""
    rows = len(weights)
    cols = len(weights[0]) if rows else 0
    if not rows or not cols:
        return []
    size = rows + cols
    maximum = max((value for row in weights for value in row if value is not None), default=0)
    costs = [[maximum] * size for _ in range(size)]
    for i in range(rows):
        for j in range(cols):
            value = weights[i][j]
            costs[i][j] = maximum - value if value is not None else maximum + 1_000_000

    # Hungarian minimum-cost assignment. The row/column scan order breaks ties.
    u = [0] * (size + 1)
    v = [0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minimum = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, size + 1):
                if used[j]:
                    continue
                current = costs[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minimum[j]:
                    minimum[j] = current
                    way[j] = j0
                if minimum[j] < delta:
                    delta = minimum[j]
                    j1 = j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assigned = [(p[j] - 1, j - 1) for j in range(1, size + 1) if p[j]]
    return sorted((i, j) for i, j in assigned if i < rows and j < cols and weights[i][j] is not None and weights[i][j] > 0)


def normalize_draft(
    draft: dict[str, Any],
    case_id: str,
    *,
    prefer_merged: bool = False,
    version: str = "DRAFT_PREVIEW_ONLY",
) -> dict[str, Any]:
    if draft.get("status") != "DRAFT_PENDING_HUMAN_REVIEW":
        raise ValueError("unexpected draft status")
    events = []
    merged = draft.get("merged_events", [])
    source = merged if prefer_merged and isinstance(merged, list) and merged else draft.get("events", [])
    for item in source:
        anomaly_types = item.get("anomaly_types")
        if isinstance(anomaly_types, list):
            normalized_types = [str(value) for value in anomaly_types if value]
            if not normalized_types:
                raise ValueError("merged draft event has no anomaly type")
            anomaly_type = normalized_types[0]
            accepted_types = normalized_types[1:]
            event_id = item.get("event_id")
        else:
            anomaly_type = str(item["anomaly_type"])
            accepted_types = []
            event_id = item.get("anomaly_id")
        events.append({
            "gt_event_id": str(event_id),
            "anomaly_type": anomaly_type,
            "entity_type": str(item["entity_type"]),
            "entity_id": str(item["entity_id"]),
            "observable_start_time": str(item["start_time"]),
            "observable_end_time": str(item["end_time"]),
            "accepted_entity_aliases": [],
            "accepted_anomaly_types": accepted_types,
        })
    if not events or len(events) > 32:
        raise ValueError("draft must contain 1 to 32 observable events")
    return {
        "case_id": case_id,
        "status": "DRAFT_PREVIEW_ONLY",
        "version": version,
        "events": events,
        "event_source": "merged_events" if source is merged else "events",
        "candidate_count": int(draft.get("candidate_count", len(draft.get("events", [])))),
        "merged_event_count": int(draft.get("merged_event_count", len(merged))),
    }


def _relevant_evidence(event: dict[str, Any], record: dict[str, Any], tolerance: int,
                       target: dict[str, Any] | None = None) -> bool:
    observed = record.get("timestamp") or record.get("start_time")
    if not observed:
        return False
    try:
        instant = _utc(str(observed))
        start = _utc(event["start_time"])
        end = _utc(event["end_time"])
    except ValueError:
        return False
    if not start.timestamp() - tolerance <= instant.timestamp() <= end.timestamp() + tolerance:
        return False
    entity = str(event["entity_id"]).strip().casefold()
    kind = str(event["entity_type"]).strip().casefold()
    if kind == "service":
        return entity in {str(record.get("service") or "").casefold(), str(record.get("parent_service") or "").casefold()}
    if kind == "pod":
        return str(record.get("pod") or "").casefold() in {
            entity, *(str(alias).casefold() for alias in (target or {}).get("accepted_entity_aliases", []))
        }
    if kind == "node":
        return entity == str(record.get("node") or "").casefold()
    if kind == "calledge":
        source = str(record.get("parent_service") or "").strip().casefold()
        target = str(record.get("service") or "").strip().casefold()
        return entity.replace(" ", "") in {f"{source}->{target}", f"{source}→{target}"}
    return False


def score_final(answer: dict[str, Any], gt: dict[str, Any], validation: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = {**DEFAULT_RUBRIC, **(rubric or {})}
    if answer.get("case_id") != gt.get("case_id"):
        raise ValueError("case mismatch")
    original = answer.get("anomalies", [])
    unique, duplicates = deduplicate_events(original)
    expected = gt["events"]
    verified: dict[int, list[dict[str, Any]]] = {}
    for check in validation.get("checks", []):
        if check.get("valid") and isinstance(check.get("verified_record"), dict):
            verified.setdefault(int(check["event"]), []).append(check["verified_record"])

    def components(i: int, j: int) -> dict[str, float | int]:
        event, target = unique[i], expected[j]
        source_index = original.index(event)
        type_score = rules["type"] if event["anomaly_type"] in {target["anomaly_type"], *target.get("accepted_anomaly_types", [])} else 0.0
        entity_score = rules["entity"] * entity_quality(event, target) / 2
        tolerance = int(rules["time_tolerance_seconds"])
        start_distance = abs((_utc(event["start_time"]) - _utc(target["observable_start_time"])).total_seconds())
        end_distance = abs((_utc(event["end_time"]) - _utc(target["observable_end_time"])).total_seconds())
        time_score = rules["time"] * (int(start_distance <= tolerance) + int(end_distance <= tolerance)) / 2
        relevant = any(_relevant_evidence(event, record, tolerance, target) for record in verified.get(source_index, []))
        evidence_score = rules["evidence"] if relevant else 0.0
        return {"prediction_index": source_index, "type": type_score, "entity": entity_score,
                "time": time_score, "evidence": evidence_score}

    credit = [[components(i, j) for j in range(len(expected))] for i in range(len(unique))]
    weights = []
    for i, event in enumerate(unique):
        row = []
        for j, target in enumerate(expected):
            weight = pair_weight(event, target)
            if (rules.get("strict_positive_credit") and
                    not any(credit[i][j][key] > 0 for key in ("type", "entity", "time", "evidence"))):
                weight = None
            row.append(weight)
        weights.append(row)
    pairs = _assignment(weights)
    matched = {i for i, _ in pairs}
    breakdown = [credit[i][j] for i, j in pairs]
    false_positives = len(unique) - len(matched)
    raw = sum(sum(item[key] for key in ("type", "entity", "time", "evidence")) for item in breakdown) / len(expected)
    penalty = rules["false_positive_penalty"] * false_positives + rules["duplicate_penalty"] * len(duplicates)
    hundred_point_score = max(0.0, min(100.0, raw - penalty))
    return {
        "status": "PREVIEW_COMPLETED" if gt.get("status") == "DRAFT_PREVIEW_ONLY" else "COMPLETED",
        "score": round(hundred_point_score * float(rules["max_points"]) / 100, 3),
        "breakdown": {"matched_events": len(pairs), "gt_events": len(expected), "false_positives": false_positives, "duplicates": len(duplicates), "event_scores": breakdown},
        "penalty": penalty,
        "grader_version": rules["version"],
        "rubric_version": rules["version"],
        "gt_version_identifier": gt.get("version", "DRAFT_PREVIEW_ONLY"),
        "gt_event_source": gt.get("event_source", "approved_events"),
        "gt_candidate_count": gt.get("candidate_count"),
        "gt_merged_event_count": gt.get("merged_event_count"),
        "gt_scored_event_count": len(expected),
        "review_status": "PREVIEW_ONLY" if gt.get("status") == "DRAFT_PREVIEW_ONLY" else "FINAL",
    }
