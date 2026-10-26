from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


COMPLETED_STATUSES = {"PREVIEW_COMPLETED", "COMPLETED"}
PENDING_STATUSES = {"RECEIVED", "VALIDATING", "GRADING", "GRADE_PENDING"}


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("server timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def decide_accuracy_speed_round(
    participants: tuple[str, str],
    submissions: dict[str, dict[str, Any] | None],
    opens_at: str,
    closes_at: str,
) -> dict[str, Any]:
    """Choose by PK anomaly score, then earliest valid server receipt."""
    if len(set(participants)) != 2:
        raise ValueError("round requires two distinct participants")
    opened = _timestamp(opens_at)
    closed = _timestamp(closes_at)
    if closed <= opened:
        raise ValueError("round close must follow open")
    entries: dict[str, dict[str, Any]] = {}
    scores: dict[str, Decimal] = {}
    for team in participants:
        row = submissions.get(team)
        status = str(row.get("status")) if row else "NOT_SUBMITTED"
        if status in PENDING_STATUSES:
            raise ValueError(f"{team}: grading is not terminal")
        grade = json.loads(row["grade_json"]) if row and row.get("grade_json") else {}
        valid = status in COMPLETED_STATUSES and grade.get("status") in COMPLETED_STATUSES
        score = Decimal("0")
        received_at = None
        elapsed = None
        if valid:
            try:
                score = Decimal(str(grade["score"]))
            except (KeyError, InvalidOperation) as exc:
                raise ValueError(f"{team}: completed grade has no numeric score") from exc
            if not score.is_finite() or not 0 <= score <= 10:
                raise ValueError(f"{team}: completed grade score is outside 0..10")
            received_at = str(row["received_at"])
            received = _timestamp(received_at)
            elapsed = (received - opened).total_seconds()
            if not opened <= received < closed:
                raise ValueError(f"{team}: scored submission is outside round window")
        scores[team] = score
        entries[team] = {
            "score": float(score),
            "submission_status": status,
            "submission_id": row.get("submission_id") if row else None,
            "valid_scored_submission": valid,
            "received_at": received_at,
            "elapsed_seconds": elapsed,
        }

    first, second = participants
    if scores[first] != scores[second]:
        winner = first if scores[first] > scores[second] else second
        basis = "ANOMALY_SCORE"
    else:
        first_at = entries[first]["received_at"]
        second_at = entries[second]["received_at"]
        if first_at and second_at:
            first_time, second_time = _timestamp(first_at), _timestamp(second_at)
            winner = first if first_time < second_time else second if second_time < first_time else None
        else:
            winner = first if first_at else second if second_at else None
        basis = "EARLIEST_VALID_SERVER_RECEIPT" if winner else "UNRESOLVED_NO_FASTER_VALID_SUBMISSION"
    return {
        "rule": "ACCURACY_THEN_SERVER_RECEIPT_V1",
        "winner": winner,
        "decision_basis": basis,
        "totals": {team: entries[team]["score"] for team in participants},
        "entries": entries,
    }
