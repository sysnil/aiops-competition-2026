from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .judge_store import JudgeResultStore
from .llm_judge import JudgeUnavailable, LLMJudge
from .matching import deduplicate_events, entity_quality, maximum_weight_matching, parse_utc


def type_points(submission: dict[str, Any], gt: dict[str, Any]) -> float:
    if "anomaly_type" in submission:
        accepted = {gt["anomaly_type"], *gt.get("accepted_anomaly_types", [])}
        return 40.0 if submission["anomaly_type"] in accepted else 0.0
    accepted = {gt["fault_type"], *gt.get("accepted_fault_type_aliases", [])}
    return 40.0 if submission["suspected_fault_type"] in accepted else 0.0


def entity_points(submission: dict[str, Any], gt: dict[str, Any]) -> float:
    return {2: 20.0, 1: 10.0, 0: 0.0}[entity_quality(submission, gt)]


def time_points(submission: dict[str, Any], gt: dict[str, Any], tolerance_seconds: int) -> float:
    submitted_start = parse_utc(submission["start_time"])
    submitted_end = parse_utc(submission["end_time"])
    gt_start = parse_utc(gt["observable_start_time"])
    gt_end = parse_utc(gt["observable_end_time"])
    points = 0.0
    if abs((submitted_start - gt_start).total_seconds()) <= tolerance_seconds:
        points += 10.0
    if abs((submitted_end - gt_end).total_seconds()) <= tolerance_seconds:
        points += 10.0
    return points


async def score_answer(
    answer: dict[str, Any],
    gt: dict[str, Any],
    evidence_validation: dict[str, Any],
    judge: LLMJudge,
    allow_preview: bool = False,
    submission_id: str = "ephemeral-submission",
    judge_store: JudgeResultStore | None = None,
    judge_semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    if gt.get("review_status") != "APPROVED" and not (
        allow_preview and gt.get("classification") == "DRAFT_PREVIEW_ONLY"
    ):
        return {"status": "PENDING_GT_APPROVAL"}
    gt_events = list(gt.get("events", []))
    if not gt_events:
        return {"status": "PENDING_GT_APPROVAL"}
    unique_events, duplicate_indices = deduplicate_events(answer["anomalies"])
    matching = maximum_weight_matching(unique_events, gt_events)
    n = len(gt_events)
    type_total = entity_total = time_total = evidence_total = 0.0
    internal_events: list[dict[str, Any]] = []
    human_review = False
    checks_by_event: dict[int, list[dict[str, Any]]] = {}
    for check in evidence_validation.get("checks", []):
        if check.get("valid"):
            checks_by_event.setdefault(int(check["event"]), []).append(check["verified_record"])

    semaphore = judge_semaphore or asyncio.Semaphore(2)
    gt_version = str(gt.get("gt_version", "unknown"))

    async def evaluate_pair(submission_index: int, gt_index: int) -> dict[str, Any]:
        submission = unique_events[submission_index]
        target = gt_events[gt_index]
        t_score = type_points(submission, target)
        e_score = entity_points(submission, target)
        w_score = time_points(submission, target, int(gt.get("time_tolerance_seconds", 120)))
        original_index = answer["anomalies"].index(submission)
        verified_records = checks_by_event.get(original_index, [])
        fingerprint_payload = {
            "submission": submission,
            "verified_evidence": verified_records,
            "rubric": str(target.get("evidence_rubric", "")),
            "model": judge.config.model,
            "prompt_version": judge.config.prompt_version,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        target_id = str(target["gt_event_id"])
        cached = judge_store.get(submission_id, gt_version, target_id, fingerprint) if judge_store else None
        if cached and cached.get("status") == "COMPLETED" and cached.get("result"):
            return {
                "gt_event_id": target_id,
                "scores": {"type": t_score, "entity": e_score, "time": w_score},
                "judge": cached["result"], "cached": True, "failure_code": None,
            }
        try:
            async with semaphore:
                judge_result = await judge.evaluate(
                    submission, verified_records, str(target.get("evidence_rubric", "")),
                )
            if judge_store:
                judge_store.record_success(submission_id, gt_version, target_id, fingerprint, judge_result)
            return {
                "gt_event_id": target_id,
                "scores": {"type": t_score, "entity": e_score, "time": w_score},
                "judge": judge_result, "cached": False, "failure_code": None,
            }
        except JudgeUnavailable as exc:
            if judge_store:
                judge_store.record_failure(
                    submission_id, gt_version, target_id, fingerprint, exc.code, str(exc),
                )
            return {
                "gt_event_id": target_id,
                "scores": {"type": t_score, "entity": e_score, "time": w_score},
                "judge": None, "cached": False, "failure_code": exc.code,
            }

    evaluated = await asyncio.gather(*(evaluate_pair(s, g) for s, g in matching.pairs))
    failure_codes: list[str] = []
    cached_count = 0
    for event_result in evaluated:
        scores = event_result["scores"]
        judge_result = event_result["judge"]
        v_score = 0.0
        if judge_result is not None:
            v_score = 20.0 * float(judge_result["evidence_score_ratio"])
            human_review = human_review or bool(judge_result["requires_human_review"])
        if event_result.get("failure_code"):
            failure_codes.append(str(event_result["failure_code"]))
        cached_count += int(bool(event_result.get("cached")))
        t_score = float(scores["type"])
        e_score = float(scores["entity"])
        w_score = float(scores["time"])
        type_total += t_score
        entity_total += e_score
        time_total += w_score
        evidence_total += v_score
        internal_events.append(
            {
                "gt_event_id": event_result["gt_event_id"],
                "scores": {"type": t_score, "entity": e_score, "time": w_score, "evidence": v_score},
                "judge": judge_result,
                "judge_cached": bool(event_result.get("cached")),
                "judge_failure_code": event_result.get("failure_code"),
            }
        )

    type_dimension = "anomaly_detection" if answer.get("schema_version") == "3.0" else "fault_type"
    breakdown = {
        type_dimension: round(type_total / n, 3),
        "entity": round(entity_total / n, 3),
        "time": round(time_total / n, 3),
        "evidence": round(evidence_total / n, 3),
    }
    extra_event_count = len(matching.unmatched_submission_indices)
    penalty = 5.0 * extra_event_count
    raw = sum(breakdown.values())
    total = max(0.0, min(100.0, raw - penalty))
    if failure_codes:
        status = "PENDING_JUDGE"
    elif human_review:
        status = "PENDING_REVIEW"
    else:
        status = "COMPLETED"
    return {
        "status": status,
        "score": round(total, 3) if status == "COMPLETED" else None,
        "breakdown": breakdown,
        "penalty": penalty,
        "extra_event_count": extra_event_count,
        "duplicate_count": len(duplicate_indices),
        "grader_version": "semifinal-anomaly-grader-2.0" if answer.get("schema_version") == "3.0" else "deterministic-grader-1.0",
        "matching_rule_version": gt.get("matching_rule_version", "entity-time-matching-1.0"),
        "time_tolerance_seconds": int(gt.get("time_tolerance_seconds", 120)),
        "rubric_version": gt.get("rubric_version", "unknown"),
        "gt_version_identifier": gt.get("gt_version", "unknown"),
        "llm_model_identifier": judge.config.model,
        "review_status": "PENDING" if status != "COMPLETED" else "FINAL",
        "judge_progress": {
            "total": len(evaluated),
            "completed": len(evaluated) - len(failure_codes),
            "failed": len(failure_codes),
            "cached": cached_count,
        },
        "judge_failure_codes": sorted(set(failure_codes)),
        "internal_events": internal_events,
    }
