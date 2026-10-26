from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def window(points: list[dict[str, Any]], start: str, end: str) -> list[float]:
    left, right = parse_utc(start), parse_utc(end)
    return [float(point["value"]) for point in points if left <= parse_utc(point["timestamp"]) < right]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build independent preview GT for a published metric-gauge practice question")
    parser.add_argument("--catalog", type=Path, default=Path(__file__).resolve().parents[2] / "config" / "questions.json")
    parser.add_argument("--public-root", type=Path, default=Path("/data/aiops-contest/public"))
    parser.add_argument("--question", default="P-Q01")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    question = next(item for item in catalog["questions"] if item["question_id"] == args.question)
    if question["publication_status"] != "PRACTICE_PUBLISHED":
        raise SystemExit("only a published practice question can produce preview GT")
    rule = question["data_processing_rule"]
    if rule["kind"] != "metric_gauge":
        raise SystemExit("this independent builder currently supports metric_gauge only")
    manifest = json.loads((args.public_root / "manifests" / f"{question['case_id']}.json").read_text(encoding="utf-8"))
    parquet = args.public_root / "cases" / question["case_id"] / manifest["version"] / "metrics.parquet"
    filters = rule["filters"]
    divisor = float(rule.get("unit_divisor", 1.0))
    connection = duckdb.connect(":memory:")
    rows = connection.execute(
        """
        SELECT strftime(
                   date_trunc('minute', timestamp AT TIME ZONE 'UTC'),
                   '%Y-%m-%dT%H:%M:%SZ'
               ) AS minute,
               avg(value / ?) AS value
        FROM read_parquet(?)
        WHERE timestamp >= ? AND timestamp < ?
          AND metric_name = ? AND pod = ?
        GROUP BY 1 ORDER BY 1
        """,
        [divisor, str(parquet), question["start_time"], question["end_time"], filters["metric_name"], filters["pod"]],
    ).fetchall()
    connection.close()
    points = [{"timestamp": str(row[0]), "value": round(float(row[1]), 6)} for row in rows]
    if not points:
        raise SystemExit("question returned no independently queryable data")
    answers: list[dict[str, Any]] = []
    for item in question["three_questions"]:
        calc = item["calculation"]
        common = {"question_index": item["index"], "unit": rule["output_unit"], "value_tolerance": 1e-6, "time_tolerance_seconds": 1.0}
        if calc["type"] == "max_with_time":
            point = max(points, key=lambda value: value["value"])
            answers.append({**common, "value": point["value"], "timestamp": point["timestamp"]})
        elif calc["type"] == "largest_adjacent_change":
            left, right = max(zip(points, points[1:]), key=lambda pair: abs(pair[1]["value"] - pair[0]["value"]))
            answers.append({**common, "value": round(right["value"] - left["value"], 6), "start_time": left["timestamp"], "end_time": right["timestamp"]})
        elif calc["type"] == "window_mean_delta":
            baseline = window(points, calc["baseline_start"], calc["baseline_end"])
            observation = window(points, calc["observation_start"], calc["observation_end"])
            answers.append({**common, "value": round(statistics.fmean(observation) - statistics.fmean(baseline), 6), "start_time": calc["observation_start"], "end_time": calc["observation_end"]})
        else:
            raise SystemExit(f"unsupported calculation: {calc['type']}")
    requirement = question["chart_requirement"]
    gt = {
        "task_type": "SEMIFINAL_QUERY_VISUALIZATION",
        "question_id": question["question_id"],
        "case_id": question["case_id"],
        "classification": "DRAFT_PREVIEW_ONLY",
        "review_status": "PREVIEW_ONLY",
        "gt_version": "P-Q01-preview-public-recompute-v1",
        "rubric_version": "semifinal-query-visualization-rubric-1.0",
        "chart_value_tolerance": 1e-6,
        "chart_time_tolerance_seconds": 0,
        "chart": {
            "chart_type": "line",
            "title": requirement["title"],
            "x_axis": requirement["x_axis"],
            "y_axis": requirement["y_axis"],
            "series": [{"name": question["target_entity_id"], "unit": rule["output_unit"], "points": points}],
        },
        "answers": answers,
        "generation": {
            "method": "independent_duckdb_public_parquet_recompute",
            "source_manifest_hash": manifest["immutable_manifest_hash"],
            "source_parquet": "metrics.parquet",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"question_id": args.question, "points": len(points), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
