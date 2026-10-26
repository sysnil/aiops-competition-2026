from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def interval_distance_seconds(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start, left_end = parse_utc(left["start_time"]), parse_utc(left["end_time"])
    right_start = parse_utc(right["observable_start_time"])
    right_end = parse_utc(right["observable_end_time"])
    if left_end >= right_start and right_end >= left_start:
        return 0.0
    return min(abs((left_end - right_start).total_seconds()), abs((right_end - left_start).total_seconds()))


def _entity_type(value: Any) -> str:
    aliases = {"CALL_EDGE": "CallEdge", "SERVICE": "Service", "POD": "Pod", "NODE": "Node"}
    text = str(value or "").strip()
    return aliases.get(text, text)


def _entity_id(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("anomaly_type") or event.get("suspected_fault_type") or "")


def entity_quality(submission: dict[str, Any], gt: dict[str, Any]) -> int:
    if _entity_type(submission["entity_type"]) == _entity_type(gt["entity_type"]) and _entity_id(submission["entity_id"]) == _entity_id(gt["entity_id"]):
        return 2
    if _entity_id(submission["entity_id"]) in {_entity_id(x) for x in gt.get("accepted_entity_aliases", [])}:
        return 2
    if gt.get("allow_service_level_partial") and _entity_type(submission["entity_type"]) == "Service":
        service_aliases = set(gt.get("accepted_service_aliases", [])) | {gt.get("service_id")}
        if _entity_id(submission["entity_id"]) in {_entity_id(x) for x in service_aliases if x}:
            return 1
    return 0


def pair_weight(submission: dict[str, Any], gt: dict[str, Any]) -> int | None:
    entity = entity_quality(submission, gt)
    distance = interval_distance_seconds(submission, gt)
    if entity == 0 and distance > 600:
        return None
    # Entity/time dominate. Type is only a deterministic tie-breaker so a wrong
    # phenomenon can still receive entity/time credit, while two simultaneous
    # phenomena on the same entity are not arbitrarily swapped.
    time_component = max(0, 1000 - int(distance))
    accepted_types = {str(gt.get("anomaly_type") or gt.get("fault_type") or "")}
    accepted_types.update(gt.get("accepted_anomaly_types", gt.get("accepted_fault_type_aliases", [])))
    type_tiebreak = 1 if _event_type(submission) in accepted_types else 0
    return entity * 100_000 + time_component * 10 + type_tiebreak


@dataclass(frozen=True)
class MatchResult:
    pairs: list[tuple[int, int]]
    unmatched_submission_indices: list[int]
    unmatched_gt_indices: list[int]


def maximum_weight_matching(submissions: list[dict[str, Any]], gt_events: list[dict[str, Any]]) -> MatchResult:
    # v3 answers and reviewed GT are both bounded to 16 events.  This keeps the
    # bit-mask dynamic program bounded while making every GT event coverable.
    if len(submissions) > 16 or len(gt_events) > 16:
        raise ValueError("matching supports at most 16 submissions and 16 GT events")
    weights = [[pair_weight(s, g) for g in gt_events] for s in submissions]

    @lru_cache(maxsize=None)
    def solve(sub_index: int, used_mask: int) -> tuple[int, tuple[tuple[int, int], ...]]:
        if sub_index >= len(submissions):
            return 0, ()
        best_score, best_pairs = solve(sub_index + 1, used_mask)
        for gt_index in range(len(gt_events)):
            weight = weights[sub_index][gt_index]
            if weight is None or used_mask & (1 << gt_index):
                continue
            tail_score, tail_pairs = solve(sub_index + 1, used_mask | (1 << gt_index))
            candidate = (weight + tail_score, ((sub_index, gt_index),) + tail_pairs)
            if candidate[0] > best_score or (candidate[0] == best_score and candidate[1] < best_pairs):
                best_score, best_pairs = candidate
        return best_score, best_pairs

    _, pairs_tuple = solve(0, 0)
    pairs = list(pairs_tuple)
    used_sub = {x for x, _ in pairs}
    used_gt = {y for _, y in pairs}
    return MatchResult(
        pairs=pairs,
        unmatched_submission_indices=[i for i in range(len(submissions)) if i not in used_sub],
        unmatched_gt_indices=[i for i in range(len(gt_events)) if i not in used_gt],
    )


def _overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    a0, a1 = parse_utc(left["start_time"]), parse_utc(left["end_time"])
    b0, b1 = parse_utc(right["start_time"]), parse_utc(right["end_time"])
    overlap = max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())
    union = max(1.0, (max(a1, b1) - min(a0, b0)).total_seconds())
    return overlap / union


def deduplicate_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    unique: list[dict[str, Any]] = []
    duplicate_indices: list[int] = []
    for index, event in enumerate(events):
        duplicate = any(
            _entity_type(event["entity_type"]) == _entity_type(existing["entity_type"])
            and _entity_id(event["entity_id"]) == _entity_id(existing["entity_id"])
            and _event_type(event) == _event_type(existing)
            and _overlap_ratio(event, existing) >= 0.8
            for existing in unique
        )
        if duplicate:
            duplicate_indices.append(index)
        else:
            unique.append(event)
    return unique, duplicate_indices
