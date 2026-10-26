from __future__ import annotations

import base64
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def _parse_time(value: Any) -> datetime:
    text = str(value or "").replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _record_time(record: dict[str, Any]) -> str | None:
    value = record.get("timestamp") or record.get("start_time") or record.get("time")
    return str(value) if value else None


def _provider_usage(run_dir: Path) -> dict[str, int] | None:
    totals: Counter[str] = Counter()
    found = False
    path = run_dir / "llm_calls.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            usage = json.loads(line).get("usage")
        except (json.JSONDecodeError, AttributeError):
            continue
        if not isinstance(usage, dict) or not usage:
            continue
        for source, target in (
            ("prompt_tokens", "input_tokens"),
            ("input_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
            ("output_tokens", "output_tokens"),
            ("total_tokens", "total_tokens"),
        ):
            value = usage.get(source)
            if isinstance(value, int):
                totals[target] += value
                found = True
    if not found:
        return None
    if "total_tokens" not in totals:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return dict(totals)


def _chart_source(anomaly: dict[str, Any]) -> tuple[str, str, list[tuple[str, float]]]:
    by_modality: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence in anomaly.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        record = evidence.get("record")
        if isinstance(record, dict):
            by_modality[str(evidence.get("modality") or "")].append(record)

    metric_points: list[tuple[str, float]] = []
    metric_name = "metric"
    for record in by_modality.get("metrics", []):
        timestamp = _record_time(record)
        try:
            value = float(record["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp:
            metric_points.append((timestamp, value))
            metric_name = str(record.get("metric_name") or metric_name)
    if metric_points:
        return metric_name, "value", sorted(metric_points, key=lambda item: _parse_time(item[0]))

    trace_points: list[tuple[str, float]] = []
    for record in by_modality.get("traces", []):
        timestamp = _record_time(record)
        try:
            value = float(record["duration_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp:
            trace_points.append((timestamp, value))
    if trace_points:
        return "Span duration", "ms", sorted(trace_points, key=lambda item: _parse_time(item[0]))

    log_counts: Counter[str] = Counter()
    for record in by_modality.get("logs", []):
        timestamp = _record_time(record)
        if timestamp:
            minute = _parse_time(timestamp).replace(second=0, microsecond=0).isoformat().replace("+00:00", "Z")
            log_counts[minute] += 1
    if log_counts:
        return "Observed log evidence", "logs/min", sorted(((key, float(value)) for key, value in log_counts.items()), key=lambda item: _parse_time(item[0]))
    return "", "", []


def _font_properties():
    from matplotlib import font_manager

    path = Path(os.getenv("AIOPS_CHART_FONT", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
    if not path.is_file():
        return None
    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path))


def _build_charts(anomalies: list[dict[str, Any]], run_dir: Path, max_charts: int = 4) -> list[dict[str, Any]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    font = _font_properties()
    matplotlib.rcParams["axes.unicode_minus"] = False
    if font is not None:
        matplotlib.rcParams["font.family"] = [font.get_name(), "DejaVu Sans"]
    charts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for anomaly in anomalies:
        name, unit, raw_points = _chart_source(anomaly)
        entity = str(anomaly.get("entity_id") or "unknown")
        if not raw_points or (entity, name) in seen:
            continue
        seen.add((entity, name))
        points = [{"timestamp": timestamp, "value": round(value, 6)} for timestamp, value in raw_points[:3600]]
        xs = [_parse_time(item["timestamp"]) for item in points]
        ys = [float(item["value"]) for item in points]
        index = len(charts) + 1
        filename = f"final-chart-{index:02d}.png"
        output = run_dir / filename
        title = f"{entity} · {name}"
        fig, axis = plt.subplots(figsize=(10, 4.5), dpi=120)
        axis.plot(xs, ys, marker="o", markersize=4, linewidth=1.8, label=entity)
        axis.set_title(title, fontproperties=font)
        axis.set_xlabel("UTC 时间", fontproperties=font)
        axis.set_ylabel(unit, fontproperties=font)
        axis.grid(True, alpha=0.25)
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=timezone.utc))
        axis.legend(prop=font)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(output, format="png", bbox_inches="tight")
        plt.close(fig)
        data = output.read_bytes()
        charts.append(
            {
                "chart_type": "line",
                "title": title,
                "x_axis": {"label": "UTC time", "unit": None, "timezone": "UTC"},
                "y_axis": {"label": name, "unit": unit, "timezone": None},
                "series": [{"name": entity, "unit": unit, "points": points}],
                "artifact": {
                    "filename": filename,
                    "media_type": "image/png",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data_base64": base64.b64encode(data).decode("ascii"),
                },
            }
        )
        if len(charts) >= max_charts:
            break
    return charts


def build_final_answer(
    intermediate: dict[str, Any],
    *,
    task: dict[str, Any],
    run_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    anomalies = intermediate.get("anomalies") if isinstance(intermediate.get("anomalies"), list) else []
    charts = _build_charts(anomalies, run_dir)
    modality_counts: Counter[str] = Counter()
    for anomaly in anomalies:
        for evidence in anomaly.get("evidence") or []:
            if isinstance(evidence, dict) and evidence.get("modality"):
                modality_counts[str(evidence["modality"])] += 1
    summary = (
        f"Agent 使用决赛 Public API 调查 {task['case_id']}，"
        f"输出 {len(anomalies)} 个有真实证据支持的可观测异常事件和 {len(charts)} 张图表。"
        f"证据模态分布：{dict(modality_counts)}。"
        "未使用 Private GT、故障注入参数或隐藏标签。"
    )
    answer: dict[str, Any] = {
        "task_type": "FINAL_ANOMALY_INVESTIGATION",
        "schema_version": "final-1.0",
        "task_id": str(task["task_id"]),
        "case_id": str(task["case_id"]),
        "run_id": run_dir.name[:128],
        "agent_status": "COMPLETED" if anomalies else "ABSTAINED",
        "anomalies": anomalies[:32],
        "charts": charts,
        "summary": summary,
        "root_hypothesis": None,
        "provider_reported_usage": _provider_usage(run_dir),
    }
    schema = json.loads((project_root / "schemas" / "final-answer-v1.schema.json").read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(answer),
        key=lambda error: list(error.path),
    )
    if errors:
        messages = [f"{'.'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors[:10]]
        raise ValueError("Final answer schema validation failed: " + "; ".join(messages))
    (run_dir / "answer.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    current_summary = {}
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        current_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    current_summary.update(
        {
            "task_type": task.get("kind"),
            "final_task_id": task["task_id"],
            "case_id": task["case_id"],
            "anomaly_count": len(anomalies),
            "chart_count": len(charts),
            "schema_valid": True,
            "provider_reported_usage": answer["provider_reported_usage"],
        }
    )
    summary_path.write_text(json.dumps(current_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return answer
