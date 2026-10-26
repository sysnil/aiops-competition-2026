from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from client.public_api import PublicAPI
import starter_todos


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def nearest_rank_percentile(values: list[float], quantile: float = 0.95) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile from an empty list")
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def bucket_start(value: str | datetime, origin: datetime, seconds: int) -> datetime:
    timestamp = parse_utc(value) if isinstance(value, str) else value.astimezone(timezone.utc)
    index = math.floor((timestamp - origin).total_seconds() / seconds)
    return origin + timedelta(seconds=index * seconds)


def bucket_labels(start: datetime, end: datetime, origin: datetime, seconds: int) -> list[datetime]:
    first = bucket_start(start, origin, seconds)
    labels: list[datetime] = []
    current = first
    while current < end:
        labels.append(current)
        current += timedelta(seconds=seconds)
    return labels


def clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


class QuestionSolver:
    """Deterministic query, chart and three-question solver.

    LLMs may improve wording outside this component, but every submitted
    numeric value and plotted point is computed from real Public API records.
    """

    def __init__(self, api: PublicAPI, run_dir: Path):
        self.api = api
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def run(self, question_id: str) -> dict[str, Any]:
        started = time.monotonic()
        question_result = self.api.get(self.api.competition_path(f"questions/{question_id}"))
        question = self._normalize_question(question_result.payload)
        rows, series = self._query_and_process(question)
        if not rows or not series or not any(item["points"] for item in series):
            raise RuntimeError("QUESTION_DATA_EMPTY: 题目指定条件未返回可绘图数据")
        answers = [self._answer(item, series, rows) for item in question["three_questions"]]
        chart_path = self.run_dir / "chart.png"
        self._plot(question, series, chart_path)
        chart_bytes = chart_path.read_bytes()
        chart_sha = hashlib.sha256(chart_bytes).hexdigest()
        request_ids = sorted({str(row["_request_id"]) for row in rows if row.get("_request_id")})
        requirement = question["chart_requirement"]
        answer = {
            "task_type": "SEMIFINAL_QUERY_VISUALIZATION",
            "schema_version": "1.0",
            "question_id": question_id,
            "case_id": question["case_id"],
            "chart": {
                "chart_type": "line",
                "title": requirement["title"],
                "x_axis": requirement["x_axis"],
                "y_axis": requirement["y_axis"],
                "series": series,
                "artifact": {
                    "filename": "chart.png",
                    "media_type": "image/png",
                    "sha256": chart_sha,
                    "data_base64": base64.b64encode(chart_bytes).decode("ascii"),
                },
            },
            "answers": answers,
            "data_source": {
                "modality": question["data_modality"],
                "start_time": question["start_time"],
                "end_time": question["end_time"],
                "filters": question["data_processing_rule"].get("filters", {}),
                "request_ids": request_ids or [question_result.request_id],
                "record_count": len(rows),
            },
            "processing": question["data_processing_rule"],
        }
        chart_data = {
            "question_id": question_id,
            "case_id": question["case_id"],
            "series": series,
            "processing": question["data_processing_rule"],
        }
        query_records = {
            "question_id": question_id,
            "request_ids": request_ids,
            "record_count": len(rows),
            "records": [clean_record(row) for row in rows],
        }
        summary = {
            "status": "complete",
            "task_type": answer["task_type"],
            "question_id": question_id,
            "case_id": question["case_id"],
            "source_record_count": len(rows),
            "chart_point_count": sum(len(item["points"]) for item in series),
            "api_request_count": self.api.request_count,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "outputs": ["answer.json", "chart.png", "chart_data.json", "query_records.json", "summary.json"],
        }
        (self.run_dir / "answer.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.run_dir / "chart_data.json").write_text(json.dumps(chart_data, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.run_dir / "query_records.json").write_text(json.dumps(query_records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (self.run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return answer

    def _paged(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.api.paged(path, params=params, max_pages=200, page_size=1000)

    @staticmethod
    def _normalize_question(question: dict[str, Any]) -> dict[str, Any]:
        """Adapt the frozen public-delivery format to the Starter contract.

        The adapter uses only fields returned by the public Question API.  It
        contains no case IDs, answers, hidden windows, or reference values.
        """
        if "query_spec" not in question:
            return question
        query = question["query_spec"]
        chart = question["chart_requirement"]
        plan = str(query["answer_plan"])
        answer_units: list[str]
        chart_unit = str(chart["y_axis"])
        if plan == "basic_mean":
            answer_units = [chart_unit, "UTC", chart_unit]
            calculations = ["max_value", "max_time", "mean"]
        elif plan == "basic_total":
            base_unit = chart_unit.removesuffix("/min")
            answer_units = [base_unit, base_unit, "UTC"]
            calculations = ["total", "max_value", "max_time"]
        elif plan == "compare":
            answer_units = ["entity ID", chart_unit, "UTC"]
            calculations = ["higher_mean_series", "absolute_series_mean_difference", "max_absolute_series_gap_time"]
        elif plan == "window_count":
            base_unit = chart_unit.removesuffix("/min")
            answer_units = [base_unit, "UTC", base_unit]
            calculations = ["total", "max_time", "range_total_difference"]
        elif plan == "window_mean":
            answer_units = [chart_unit, "UTC", chart_unit]
            calculations = ["max_value", "max_time", "range_mean_difference"]
        else:
            raise ValueError(f"unsupported public answer plan: {plan}")
        kind = str(query["kind"])
        prompts = []
        for index, item in enumerate(question["questions"]):
            prompts.append({
                "index": index + 1,
                "text": item["text"],
                "calculation": {
                    "type": calculations[index],
                    "unit": answer_units[index],
                    # Public metadata only.  Starter TODO handlers use this to
                    # choose the right generic implementation; it is not a
                    # question ID and contains no hidden answer.
                    "source_kind": kind,
                    "comparison_minute_ranges": query.get("comparison_minute_ranges"),
                },
            })
        filters = dict(query.get("filters", {}))
        entities = list(filters.get("entities", chart.get("series", [])))
        starter_x_axis, starter_y_axis = starter_todos.chart_axes(chart)
        return {
            **question,
            "data_modality": str(question["modality"]).lower(),
            "metric_or_operation": filters.get("metric_name") or filters.get("operation_name"),
            "start_time": question["time_range"]["start_utc"],
            "end_time": question["time_range"]["end_exclusive_utc"],
            "chart_requirement": {
                "chart_type": chart["type"],
                "title": question["title"],
                "x_axis": starter_x_axis,
                "y_axis": starter_y_axis,
                "legend_required": chart["legend_required"],
            },
            "three_questions": prompts,
            "data_processing_rule": {
                "kind": kind,
                "filters": filters,
                "series_entities": entities,
                "bucket_seconds": int(query["time_bucket"]["width_seconds"]),
                "bucket_origin": query["time_bucket"]["origin_utc"],
                "minimum_samples_per_minute": int(query["minimum_samples_per_minute"]),
                "missing_policy": query["missing_policy"],
                "counter_reset_policy": query["counter_reset_policy"],
                "p95_definition": query["p95_definition"],
                "comparison_minute_ranges": query.get("comparison_minute_ranges"),
                "output_unit": chart_unit,
                "answer_plan": plan,
            },
        }

    def _query_and_process(self, question: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rule = question["data_processing_rule"]
        kind = rule["kind"]
        if kind in {"memory", "cpu_rate", "server_span_count", "error_log_count", "edge_error_count", "server_p95", "edge_p95"}:
            return self._query_frozen(question)
        case_id = question["case_id"]
        start, end = question["start_time"], question["end_time"]
        start_dt, end_dt = parse_utc(start), parse_utc(end)
        bucket_seconds = int(rule.get("bucket_seconds", 60))
        if bucket_seconds <= 0 or bucket_seconds > 3600:
            raise ValueError("bucket_seconds must be between 1 and 3600")
        # New frozen questions must publish bucket_origin (the exact Case
        # start).  The fallback preserves the legacy P-Q01 natural-minute
        # contract and therefore does not invalidate its historical scores.
        origin = parse_utc(str(rule["bucket_origin"])) if rule.get("bucket_origin") else start_dt.replace(second=0, microsecond=0)
        labels = bucket_labels(start_dt, end_dt, origin, bucket_seconds)
        params = {"start": start, "end": end, **rule.get("filters", {})}
        if kind.startswith("metric_"):
            rows = self._paged(self.api.competition_path(f"cases/{case_id}/metrics"), {**params, "aggregation": "raw"})
        elif kind == "log_count":
            rows = self._paged(self.api.competition_path(f"cases/{case_id}/logs"), params)
        elif kind == "comparison_p95":
            rows = []
            for service in rule["services"]:
                rows.extend(self._paged(self.api.competition_path(f"cases/{case_id}/traces"), {**params, "service": service}))
        else:
            rows = self._paged(self.api.competition_path(f"cases/{case_id}/traces"), params)

        unit = str(rule.get("output_unit", question["chart_requirement"]["y_axis"].get("unit", "count")))
        target = str(question.get("target_entity_id") or question.get("metric_or_operation") or "series")
        if kind == "metric_gauge":
            divisor = float(rule.get("unit_divisor", 1.0))
            buckets: dict[datetime, list[float]] = defaultdict(list)
            for row in rows:
                if row.get("value") is not None:
                    buckets[bucket_start(str(row["timestamp"]), origin, bucket_seconds)].append(float(row["value"]) / divisor)
            values = {key: statistics.fmean(buckets[key]) if buckets.get(key) else None for key in labels}
            return rows, [self._series(target, unit, values)]
        if kind == "metric_counter_rate":
            grouped: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
            for row in rows:
                identity_labels = json.dumps(row.get("labels") or {}, sort_keys=True, ensure_ascii=False)
                identity = "|".join(str(row.get(key) or "") for key in ("metric_name", "service", "pod", "node")) + identity_labels
                grouped[identity].append((parse_utc(str(row["timestamp"])), float(row["value"])))
            rates: dict[datetime, list[float]] = defaultdict(list)
            for samples in grouped.values():
                samples.sort()
                for (left_time, left), (right_time, right) in zip(samples, samples[1:]):
                    seconds = (right_time - left_time).total_seconds()
                    if seconds <= 0:
                        continue
                    if right < left:
                        reset_rule = str(rule.get("counter_reset_rule", "drop_negative_delta"))
                        if reset_rule in {"use_current_after_reset", "non_negative_delta"}:
                            delta = right
                        else:
                            continue
                    else:
                        delta = right - left
                    rates[bucket_start(right_time, origin, bucket_seconds)].append(delta / seconds)
            aggregation = str(rule.get("series_aggregation", "sum"))
            values = {
                key: ((statistics.fmean(rates[key]) if aggregation == "mean" else sum(rates[key])) if rates.get(key) else None)
                for key in labels
            }
            return rows, [self._series(target, unit, values)]
        timestamp_field = "timestamp" if kind == "log_count" else "start_time"
        if kind in {"trace_count", "log_count", "call_edge_failure_count"}:
            counts: dict[datetime, float] = defaultdict(float)
            for row in rows:
                counts[bucket_start(str(row[timestamp_field]), origin, bucket_seconds)] += 1.0
            return rows, [self._series(target, unit, {key: counts.get(key, 0.0) for key in labels})]
        if kind == "trace_error_rate":
            totals: dict[datetime, int] = defaultdict(int)
            errors: dict[datetime, int] = defaultdict(int)
            for row in rows:
                bucket = bucket_start(str(row["start_time"]), origin, bucket_seconds)
                totals[bucket] += 1
                errors[bucket] += int(str(row.get("status") or "").upper() == str(rule.get("error_status", "ERROR")).upper())
            values = {key: (100.0 * errors[key] / totals[key] if totals.get(key) else None) for key in labels}
            return rows, [self._series(target, unit, values)]
        if kind in {"trace_p95", "call_edge_p95"}:
            buckets: dict[datetime, list[float]] = defaultdict(list)
            for row in rows:
                if row.get("duration_ms") is not None:
                    buckets[bucket_start(str(row["start_time"]), origin, bucket_seconds)].append(float(row["duration_ms"]))
            return rows, [self._series(target, unit, {key: nearest_rank_percentile(buckets[key]) if buckets.get(key) else None for key in labels})]
        if kind == "comparison_p95":
            by_service: dict[str, dict[datetime, list[float]]] = defaultdict(lambda: defaultdict(list))
            for row in rows:
                service = str(row.get("service") or "unknown")
                by_service[service][bucket_start(str(row["start_time"]), origin, bucket_seconds)].append(float(row["duration_ms"]))
            series = [
                self._series(service, unit, {key: nearest_rank_percentile(buckets[key]) if buckets.get(key) else None for key in labels})
                for service, buckets in sorted(by_service.items())
            ]
            return rows, series
        raise ValueError(f"unsupported question processing kind: {kind}")

    def _query_frozen(self, question: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Execute a frozen semifinal question through the real Public API."""
        rule = question["data_processing_rule"]
        kind = str(rule["kind"])
        case_id = str(question["case_id"])
        start, end = str(question["start_time"]), str(question["end_time"])
        start_dt, end_dt = parse_utc(start), parse_utc(end)
        origin = parse_utc(str(rule["bucket_origin"]))
        bucket_seconds = int(rule["bucket_seconds"])
        labels = bucket_labels(start_dt, end_dt, origin, bucket_seconds)
        entities = [str(value) for value in rule["series_entities"]]
        public_filters = dict(rule.get("filters", {}))
        rows: list[dict[str, Any]] = []

        for entity in entities:
            params: dict[str, Any] = {"start": start, "end": end}
            if kind in {"memory", "cpu_rate"}:
                params.update({"metric_name": public_filters["metric_name"], "pod": entity, "aggregation": "raw"})
                path = self.api.competition_path(f"cases/{case_id}/metrics")
            elif kind == "error_log_count":
                params.update({"service": entity, "severity": "ERROR"})
                path = self.api.competition_path(f"cases/{case_id}/logs")
            else:
                if kind.startswith("edge_"):
                    parent, child = (part.strip() for part in entity.split("->", 1))
                    params.update({"service": child, "parent_service": parent})
                else:
                    params["service"] = entity
                operation = public_filters.get("operation_name")
                if operation:
                    params["operation_name"] = operation
                if kind == "edge_error_count":
                    params["status"] = "ERROR"
                path = self.api.competition_path(f"cases/{case_id}/traces")
            for row in self._paged(path, params):
                row["_series_name"] = entity
                rows.append(row)

        unit = str(rule["output_unit"])
        minimum = int(rule["minimum_samples_per_minute"])
        by_entity: dict[str, dict[datetime, list[float]]] = {
            entity: defaultdict(list) for entity in entities
        }
        if kind in {"memory", "cpu_rate"}:
            for entity in entities:
                samples = sorted(
                    (
                        parse_utc(str(row["timestamp"])),
                        float(row["value"]),
                    )
                    for row in rows
                    if row.get("_series_name") == entity
                    and row.get("value") is not None
                    and self._container_matches(row, public_filters.get("container"))
                )
                previous: tuple[datetime, float] | None = None
                for timestamp, value in samples:
                    bucket = bucket_start(timestamp, origin, bucket_seconds)
                    if kind == "memory":
                        by_entity[entity][bucket].append(value / 1048576.0)
                    elif previous is not None:
                        elapsed = (timestamp - previous[0]).total_seconds()
                        delta = value - previous[1]
                        if elapsed > 0 and delta >= 0:
                            by_entity[entity][bucket].append(delta / elapsed)
                    previous = (timestamp, value)
        elif kind == "error_log_count":
            for row in rows:
                if str(row.get("severity") or "").upper() == "ERROR":
                    by_entity[str(row["_series_name"])][bucket_start(str(row["timestamp"]), origin, bucket_seconds)].append(1.0)
        else:
            for row in rows:
                if not self._is_server_span(row):
                    continue
                if kind == "edge_error_count" and str(row.get("status") or "").upper() != "ERROR":
                    continue
                value = row.get("duration_ms") if kind.endswith("_p95") else 1.0
                if kind.endswith("_p95") and (value is None or not math.isfinite(float(value)) or float(value) < 0):
                    continue
                by_entity[str(row["_series_name"])][bucket_start(str(row["start_time"]), origin, bucket_seconds)].append(float(value))

        series: list[dict[str, Any]] = []
        for entity in entities:
            values: dict[datetime, float | None] = {}
            for label in labels:
                samples = by_entity[entity].get(label, [])
                if kind in {"memory", "cpu_rate"}:
                    values[label] = statistics.fmean(samples) if len(samples) >= minimum else None
                elif kind.endswith("_p95"):
                    values[label] = nearest_rank_percentile(samples) if len(samples) >= minimum else None
                else:
                    values[label] = float(len(samples))
            series.append(self._series(entity, unit, values))
        return rows, series

    @staticmethod
    def _container_matches(row: dict[str, Any], expected: Any) -> bool:
        if not expected:
            return True
        labels = row.get("labels") or {}
        if isinstance(labels, str):
            try:
                labels = json.loads(labels)
            except json.JSONDecodeError:
                return False
        return isinstance(labels, dict) and str(labels.get("container")) == str(expected)

    @staticmethod
    def _is_server_span(row: dict[str, Any]) -> bool:
        attributes = row.get("attributes") or {}
        if isinstance(attributes, str):
            try:
                attributes = json.loads(attributes)
            except json.JSONDecodeError:
                return False
        return isinstance(attributes, dict) and str(attributes.get("span.kind", "")).lower() == "server"

    @staticmethod
    def _series(name: str, unit: str, values: dict[datetime, float | None]) -> dict[str, Any]:
        return {
            "name": name,
            "unit": unit,
            "points": [{"timestamp": iso_utc(key), "value": None if value is None else float(value)} for key, value in sorted(values.items())],
        }

    def _answer(self, question: dict[str, Any], series: list[dict[str, Any]], rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        calc = question.get("calculation", {})
        kind = calc.get("type")
        source_kind = str(calc.get("source_kind") or "")
        selected = next((item for item in series if item["name"] == calc.get("series")), series[0])
        points = selected["points"]
        valid_points = [point for point in points if point.get("value") is not None]
        unit = selected["unit"]
        common = {"question_index": int(question["index"]), "answer_type": str(kind), "unit": str(calc.get("unit", unit))}
        # The starter still computes and plots the real CPU series so teams can
        # inspect it.  Turning that series into robust answers is intentionally
        # left as TODO-02; never substitute a guessed numeric value.
        if source_kind == "cpu_rate":
            try:
                return {**common, **starter_todos.cpu_answer(str(kind), points, common["unit"])}
            except starter_todos.TodoNotImplemented:
                return self._not_implemented(common, "TODO-02", "CPU Counter 速率作答尚未实现")
        # TODO-03: implement generic two-series mean and maximum-gap answers.
        if kind in {"higher_mean_series", "absolute_series_mean_difference", "max_absolute_series_gap_time"}:
            try:
                return {**common, **starter_todos.compare_series_answer(str(kind), series, common["unit"])}
            except starter_todos.TodoNotImplemented:
                return self._not_implemented(common, "TODO-03", "多序列比较作答尚未实现")
        # TODO-04/05: the starter exposes the real plotted values, but leaves
        # cross-window comparison for participants.  Edge questions are kept
        # separate because they also need Parent/Service relationship checks.
        if kind in {"range_total_difference", "range_mean_difference"}:
            todo = "TODO-05" if source_kind.startswith("edge_") else "TODO-04"
            try:
                if todo == "TODO-05":
                    payload = starter_todos.edge_window_answer(
                        points,
                        calc.get("comparison_minute_ranges") or [],
                        "total" if kind == "range_total_difference" else "mean",
                        common["unit"],
                    )
                else:
                    payload = starter_todos.p95_window_answer(
                        points,
                        calc.get("comparison_minute_ranges") or [],
                        common["unit"],
                    )
                return {**common, **payload}
            except starter_todos.TodoNotImplemented:
                label = "调用边窗口比较尚未实现" if todo == "TODO-05" else "p95 窗口比较尚未实现"
                return self._not_implemented(common, todo, label)
        if kind == "max_value":
            point = max(valid_points, key=lambda item: item["value"])
            value = self._rounded_answer(point["value"], common["unit"])
            return {**common, "value": value, "explanation": f"最大值为 {value} {common['unit']}。"}
        if kind == "max_time":
            point = max(valid_points, key=lambda item: item["value"])
            value = parse_utc(point["timestamp"]).isoformat()
            return {**common, "value": value, "timestamp": point["timestamp"], "explanation": "返回最早峰值分钟的 UTC 起点。"}
        if kind == "mean":
            value = self._rounded_answer(statistics.fmean(float(point["value"]) for point in valid_points), common["unit"])
            return {**common, "value": value, "explanation": f"有效分钟值的算术平均为 {value} {common['unit']}。"}
        if kind == "max_with_time":
            point = max(valid_points, key=lambda item: item["value"])
            return {**common, "value": point["value"], "timestamp": point["timestamp"], "explanation": f"最大值为 {point['value']} {unit}。"}
        if kind == "min_with_time":
            point = min(valid_points, key=lambda item: item["value"])
            return {**common, "value": point["value"], "timestamp": point["timestamp"], "explanation": f"最小值为 {point['value']} {unit}。"}
        if kind == "largest_adjacent_change":
            pairs = [(left, right) for left, right in zip(points, points[1:]) if left.get("value") is not None and right.get("value") is not None]
            if not pairs:
                raise RuntimeError("not enough chart points for adjacent-change calculation")
            left, right = max(pairs, key=lambda pair: abs(pair[1]["value"] - pair[0]["value"]))
            delta = round(right["value"] - left["value"], 6)
            return {**common, "value": delta, "start_time": left["timestamp"], "end_time": right["timestamp"], "explanation": f"该相邻区间变化量为 {delta} {unit}。"}
        if kind == "window_mean_delta":
            baseline = self._window_values(points, calc["baseline_start"], calc["baseline_end"])
            observation = self._window_values(points, calc["observation_start"], calc["observation_end"])
            if not baseline or not observation:
                raise RuntimeError("question windows do not contain chart points")
            delta = round(statistics.fmean(observation) - statistics.fmean(baseline), 6)
            return {**common, "value": delta, "start_time": calc["observation_start"], "end_time": calc["observation_end"], "explanation": f"观察窗口均值减去基线窗口均值为 {delta} {unit}。"}
        if kind == "mean_in_window":
            values = self._window_values(points, calc["start"], calc["end"])
            if not values:
                raise RuntimeError("answer window does not contain chart points")
            value = round(statistics.fmean(values), 6)
            return {**common, "value": value, "start_time": calc["start"], "end_time": calc["end"], "explanation": f"指定窗口均值为 {value} {unit}。"}
        if kind == "higher_mean_window":
            windows = calc.get("windows", [])
            candidates: list[tuple[str, str, str, float]] = []
            for window in windows:
                values = self._window_values(points, window["start"], window["end"])
                if values:
                    candidates.append((str(window["label"]), window["start"], window["end"], statistics.fmean(values)))
            if not candidates:
                raise RuntimeError("comparison windows do not contain chart points")
            label, start, end, mean = max(candidates, key=lambda item: item[3])
            return {**common, "value": label, "start_time": start, "end_time": end, "explanation": f"{label} 的均值最高（{round(mean, 6)} {unit}）。"}
        if kind == "max_series":
            peaks = [(item["name"], max((point for point in item["points"] if point.get("value") is not None), key=lambda point: point["value"])) for item in series if any(point.get("value") is not None for point in item.get("points", []))]
            name, point = max(peaks, key=lambda item: item[1]["value"])
            return {**common, "value": name, "timestamp": point["timestamp"], "explanation": f"{name} 的峰值最高，为 {point['value']} {unit}。"}
        if kind == "series_peaks":
            peaks = {item["name"]: max((point for point in item["points"] if point.get("value") is not None), key=lambda point: point["value"]) for item in series if any(point.get("value") is not None for point in item.get("points", []))}
            value = json.dumps(peaks, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return {**common, "value": value, "explanation": "已计算每条曲线的峰值与 UTC 时间。"}
        if kind == "series_mean_difference":
            by_name = {item["name"]: item["points"] for item in series}
            left_name, right_name = calc["left_series"], calc["right_series"]
            left = self._window_values(by_name[left_name], calc["start"], calc["end"])
            right = self._window_values(by_name[right_name], calc["start"], calc["end"])
            if not left or not right:
                raise RuntimeError("series comparison window does not contain chart points")
            value = round(statistics.fmean(left) - statistics.fmean(right), 6)
            return {**common, "value": value, "start_time": calc["start"], "end_time": calc["end"], "explanation": f"{left_name} 减去 {right_name} 的窗口均值为 {value} {unit}。"}
        if kind == "raw_record_count":
            selected_rows = list(rows or [])
            status = calc.get("status")
            if status is not None:
                selected_rows = [row for row in selected_rows if str(row.get("status") or "").upper() == str(status).upper()]
            if calc.get("start") and calc.get("end"):
                start, end = parse_utc(calc["start"]), parse_utc(calc["end"])
                timestamp_field = calc.get("timestamp_field", "start_time")
                selected_rows = [row for row in selected_rows if row.get(timestamp_field) and start <= parse_utc(str(row[timestamp_field])) < end]
            return {**common, "value": len(selected_rows), "unit": "records", "explanation": f"指定条件共有 {len(selected_rows)} 条真实记录。"}
        if kind == "longest_positive_range":
            positive = [point for point in points if point.get("value") is not None and float(point["value"]) > 0]
            best: list[dict[str, Any]] = []
            current: list[dict[str, Any]] = []
            for point in positive:
                if current and (parse_utc(point["timestamp"]) - parse_utc(current[-1]["timestamp"])).total_seconds() > 60:
                    if len(current) > len(best):
                        best = current
                    current = []
                current.append(point)
            if len(current) > len(best):
                best = current
            if not best:
                return {**common, "value": 0, "unit": "minutes", "explanation": "未发现正值连续区间。"}
            return {**common, "value": len(best), "unit": "minutes", "start_time": best[0]["timestamp"], "end_time": best[-1]["timestamp"], "explanation": f"最长连续正值区间包含 {len(best)} 个分钟点。"}
        if kind == "total":
            value = round(sum(point["value"] for point in valid_points), 6)
            return {**common, "value": value, "explanation": f"所有分钟值合计为 {value} {unit}。"}
        if kind == "sample_count":
            return {**common, "value": len(valid_points), "unit": "points", "explanation": f"共 {len(valid_points)} 个有效分钟点。"}
        raise ValueError(f"unsupported answer calculation: {kind}")

    @staticmethod
    def _not_implemented(common: dict[str, Any], todo: str, detail: str) -> dict[str, Any]:
        """Return a schema-valid, explicit unfinished result.

        A visible marker is preferable to a fabricated number.  The public
        scorer will award zero for this one answer while continuing to score
        the real chart and the other implemented answers.
        """
        return {
            **common,
            "value": f"NOT_IMPLEMENTED:{todo}",
            "explanation": f"{detail}；请按 {todo} 指南完成后重新运行。",
        }

    @staticmethod
    def _rounded_answer(value: float, unit: str) -> int | float:
        if unit in {"logs", "spans", "error spans"}:
            return int(round(float(value)))
        digits = 4 if unit == "cores" else 2
        return round(float(value), digits)

    @staticmethod
    def _window_values(points: list[dict[str, Any]], start: str, end: str) -> list[float]:
        left, right = parse_utc(start), parse_utc(end)
        return [float(item["value"]) for item in points if item.get("value") is not None and left <= parse_utc(item["timestamp"]) < right]

    @staticmethod
    def _plot(question: dict[str, Any], series: list[dict[str, Any]], path: Path) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from matplotlib import font_manager
        except ImportError as exc:
            raise RuntimeError("MATPLOTLIB_UNAVAILABLE: 请安装 Starter requirements") from exc
        font_path = Path(os.getenv("AIOPS_CHART_FONT", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
        font_properties = None
        if font_path.is_file():
            font_manager.fontManager.addfont(str(font_path))
            font_properties = font_manager.FontProperties(fname=str(font_path))
            matplotlib.rcParams["font.family"] = [font_properties.get_name(), "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        requirement = question["chart_requirement"]
        plt.style.use("seaborn-v0_8-whitegrid")
        figure, axis = plt.subplots(figsize=(12.8, 7.2), dpi=120)
        for item in series:
            x_values = [parse_utc(point["timestamp"]) for point in item["points"]]
            y_values = [point["value"] for point in item["points"]]
            axis.plot(x_values, y_values, marker="o", markersize=3, linewidth=2, label=item["name"])
        axis.set_title(requirement["title"], fontsize=16, fontproperties=font_properties)
        axis.set_xlabel(f"{requirement['x_axis']['label']} ({requirement['x_axis'].get('timezone', 'UTC')})", fontproperties=font_properties)
        axis.set_ylabel(f"{requirement['y_axis']['label']} ({requirement['y_axis'].get('unit', series[0]['unit'])})", fontproperties=font_properties)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
        if len(series) > 1 or requirement.get("legend_required"):
            axis.legend(loc="best", prop=font_properties)
        figure.autofmt_xdate()
        figure.tight_layout()
        figure.savefig(path, format="png", metadata={"Software": "AIOps Participant Starter"})
        plt.close(figure)
