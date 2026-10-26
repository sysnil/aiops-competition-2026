from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from grading.private_grader.llm_judge import JudgeConfig, JudgeUnavailable, LLMJudge
from grading.private_grader.matching import deduplicate_events, maximum_weight_matching, parse_utc
from grading.private_grader.scoring import entity_points, time_points


ENTITY_TYPE_MAP = {"SERVICE": "Service", "POD": "Pod", "CALL_EDGE": "Network"}
DRAFT_RUBRIC = (
    "This is a non-official observational draft review. Evaluate only whether the verified Public "
    "telemetry demonstrates a material anomaly for the contestant's submitted entity and interval. "
    "Do not infer an injected fault, causal mechanism, or hidden target. FULL requires multiple or "
    "strong directly aligned abnormal signals; PARTIAL means relevant but incomplete or ambiguous; "
    "NONE means normal, irrelevant, contradictory, or not entity/time aligned."
)


def load_submission(database: Path, submission_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT answer_path, validation_json, case_id FROM submissions WHERE submission_id=?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise ValueError("submission not found")
    if row["case_id"] != "SF-001":
        raise ValueError("draft evaluator accepts only an SF-001 submission")
    validation = json.loads(row["validation_json"] or "null")
    if not isinstance(validation, dict) or not validation.get("valid"):
        raise ValueError("submission does not have successful evidence validation")
    return json.loads(Path(row["answer_path"]).read_text(encoding="utf-8")), validation


def validate_and_convert_draft(draft: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if draft.get("status") != "DRAFT_PENDING_HUMAN_REVIEW":
        raise ValueError("unexpected draft status")
    if draft.get("formal_scoring_gt") is not False:
        raise ValueError("input must be explicitly marked as non-formal scoring GT")
    if draft.get("source_public_manifest_sha256") != manifest.get("source_manifest_sha256"):
        raise ValueError("draft source manifest does not match the published Case manifest")
    case_start, case_end = parse_utc(manifest["start_time"]), parse_utc(manifest["end_time"])
    converted: list[dict[str, Any]] = []
    events = draft.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("draft has no events")
    for index, event in enumerate(events):
        if event.get("review_status") != "DRAFT_PENDING_HUMAN_REVIEW":
            raise ValueError("draft event has an unexpected review status")
        start, end = parse_utc(event["start_time"]), parse_utc(event["end_time"])
        if start > end or start < case_start or end > case_end:
            raise ValueError("draft event is outside the published Case window")
        entity_type = ENTITY_TYPE_MAP.get(str(event.get("entity_type")))
        if not entity_type:
            raise ValueError("unsupported draft entity type")
        converted.append(
            {
                "gt_event_id": str(event.get("anomaly_id") or f"DRAFT-{index + 1}"),
                "entity_type": entity_type,
                "entity_id": str(event["entity_id"]),
                "accepted_entity_aliases": [],
                "observable_start_time": event["start_time"],
                "observable_end_time": event["end_time"],
                "evidence_rubric": DRAFT_RUBRIC,
            }
        )
    return converted


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    draft_bytes = args.draft.read_bytes()
    draft = json.loads(draft_bytes)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    answer, validation = load_submission(args.database, args.submission_id)
    targets = validate_and_convert_draft(draft, manifest)
    events, duplicate_indices = deduplicate_events(answer["anomalies"])
    matching = maximum_weight_matching(events, targets)
    checks_by_event: dict[int, list[dict[str, Any]]] = {}
    for check in validation.get("checks", []):
        if check.get("valid") and "verified_record" in check:
            checks_by_event.setdefault(int(check["event"]), []).append(check["verified_record"])

    judge = LLMJudge(JudgeConfig.from_env())
    n = len(targets)
    entity_total = time_total = evidence_total = 0.0
    judge_failed = False
    human_review_requested = False
    private_event_results: list[dict[str, Any]] = []
    for submission_index, target_index in matching.pairs:
        submitted = events[submission_index]
        target = targets[target_index]
        entity_score = entity_points(submitted, target)
        time_score = time_points(submitted, target, args.time_tolerance_seconds)
        evidence_score = 0.0
        judge_result = None
        try:
            original_index = answer["anomalies"].index(submitted)
            judge_result = await judge.evaluate(
                submitted,
                checks_by_event.get(original_index, []),
                DRAFT_RUBRIC,
            )
            evidence_score = 20.0 * float(judge_result["evidence_score_ratio"])
            human_review_requested = human_review_requested or bool(
                judge_result["requires_human_review"]
            )
        except JudgeUnavailable:
            judge_failed = True
        entity_total += entity_score
        time_total += time_score
        evidence_total += evidence_score
        private_event_results.append(
            {
                "draft_event_id": target["gt_event_id"],
                "submitted_event_index": answer["anomalies"].index(submitted),
                "entity_points": entity_score,
                "time_points": time_score,
                "evidence_points": evidence_score,
                "judge": judge_result,
            }
        )

    breakdown = {
        "fault_type": None,
        "entity": round(entity_total / n, 3),
        "time": round(time_total / n, 3),
        "evidence": round(evidence_total / n, 3),
    }
    penalty = 5.0 * len(matching.unmatched_submission_indices)
    provisional = max(0.0, min(60.0, sum(v for v in breakdown.values() if v is not None) - penalty))
    return {
        "evaluation_mode": "DRAFT_NON_OFFICIAL",
        "status": "PENDING_JUDGE" if judge_failed else "PENDING_REVIEW",
        "formal_score": None,
        "provisional_score_out_of_60": round(provisional, 3) if not judge_failed else None,
        "breakdown": breakdown,
        "fault_type_note": "Not scored: the draft contains observed symptoms, not approved causal fault labels.",
        "penalty": penalty,
        "submission_id": args.submission_id,
        "case_id": "SF-001",
        "draft_sha256": hashlib.sha256(draft_bytes).hexdigest(),
        "draft_status": draft.get("status"),
        "draft_event_count": n,
        "matched_event_count": len(matching.pairs),
        "unmatched_draft_event_count": len(matching.unmatched_gt_indices),
        "unmatched_submission_event_count": len(matching.unmatched_submission_indices),
        "duplicate_submission_event_count": len(duplicate_indices),
        "judge_model": judge.config.model,
        "judge_prompt_version": judge.config.prompt_version,
        "requires_human_review": True,
        "judge_requested_human_review": human_review_requested,
        "private_event_results": private_event_results,
        "disclaimer": "Draft-only test. Must not be returned as an official score or placed on a leaderboard.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a private, non-official SF-001 draft evaluation")
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-tolerance-seconds", type=int, default=120)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.chmod(0o640)
    public = {key: value for key, value in result.items() if key != "private_event_results"}
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
