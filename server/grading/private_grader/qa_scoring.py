from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def _close(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(right, (int, float)) and not isinstance(right, bool):
        try:
            return abs(float(left) - float(right)) <= tolerance
        except (TypeError, ValueError):
            return False
    return str(left).strip().casefold() == str(right).strip().casefold()


def score_qa_answer(answer: dict[str, Any], gt: dict[str, Any]) -> dict[str, Any]:
    """Deterministically score one 10-point query/visualization question."""
    expected_chart = gt["chart"]
    submitted_chart = answer["chart"]
    chart_breakdown = {"identity": 0.0, "axes": 0.0, "data": 0.0}

    expected_names = [item["name"] for item in expected_chart["series"]]
    submitted_names = [item["name"] for item in submitted_chart["series"]]
    # Public titles may be descriptive or localized; grade semantic chart
    # identity from chart type and series, never a private GT title string.
    if (
        submitted_chart.get("chart_type") == expected_chart.get("chart_type")
        and submitted_names == expected_names
    ):
        chart_breakdown["identity"] = 0.75
    if (
        submitted_chart.get("x_axis") == expected_chart.get("x_axis")
        and submitted_chart.get("y_axis") == expected_chart.get("y_axis")
    ):
        chart_breakdown["axes"] = 0.75

    point_tolerance = float(gt.get("chart_value_tolerance", 1e-6))
    time_tolerance = float(gt.get("chart_time_tolerance_seconds", 0.0))
    data_ok = len(submitted_chart["series"]) == len(expected_chart["series"])
    if data_ok:
        for submitted_series, expected_series in zip(submitted_chart["series"], expected_chart["series"]):
            submitted_points = submitted_series.get("points", [])
            expected_points = expected_series.get("points", [])
            if submitted_series.get("unit") != expected_series.get("unit") or len(submitted_points) != len(expected_points):
                data_ok = False
                break
            for submitted_point, expected_point in zip(submitted_points, expected_points):
                try:
                    timestamp_ok = abs((_utc(submitted_point["timestamp"]) - _utc(expected_point["timestamp"])).total_seconds()) <= time_tolerance
                except (KeyError, TypeError, ValueError):
                    timestamp_ok = False
                if not timestamp_ok or not _close(submitted_point.get("value"), expected_point.get("value"), point_tolerance):
                    data_ok = False
                    break
            if not data_ok:
                break
    if data_ok:
        chart_breakdown["data"] = 2.5

    submitted_answers = {int(item["question_index"]): item for item in answer["answers"]}
    question_scores: list[float] = []
    answer_results: list[dict[str, Any]] = []
    for expected in gt["answers"]:
        index = int(expected["question_index"])
        submitted = submitted_answers.get(index, {})
        tolerance = float(expected.get("value_tolerance", 1e-6))
        value_ok = _close(submitted.get("value"), expected.get("value"), tolerance)
        unit_ok = submitted.get("unit") == expected.get("unit")
        time_ok = True
        for field in ("timestamp", "start_time", "end_time"):
            if expected.get(field) is not None:
                try:
                    time_ok = time_ok and abs((_utc(str(submitted.get(field))) - _utc(str(expected[field]))).total_seconds()) <= float(expected.get("time_tolerance_seconds", 1.0))
                except (TypeError, ValueError):
                    time_ok = False
        score = 2.0 if value_ok and unit_ok and time_ok else 0.0
        question_scores.append(score)
        answer_results.append({"question_index": index, "score": score, "value_ok": value_ok, "unit_ok": unit_ok, "time_ok": time_ok})

    chart_score = round(sum(chart_breakdown.values()), 3)
    total = round(chart_score + sum(question_scores), 3)
    return {
        "status": "COMPLETED",
        "score": total,
        "breakdown": {
            "chart": chart_score,
            "question_1": question_scores[0],
            "question_2": question_scores[1],
            "question_3": question_scores[2],
        },
        "penalty": 0.0,
        "grader_version": "semifinal-query-visualization-grader-1.1",
        "rubric_version": gt.get("rubric_version", "unknown") + "/chart-title-independent-v1",
        "gt_version_identifier": gt.get("gt_version", "unknown"),
        "llm_model_identifier": None,
        "review_status": "FINAL",
        "judge_progress": {"total": 0, "completed": 0, "failed": 0, "cached": 0},
        "internal_chart": chart_breakdown,
        "internal_answers": answer_results,
    }
