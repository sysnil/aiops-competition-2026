from __future__ import annotations

import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from agent.evidence import evidence_from_record, validate_evidence
from agent.mcp_client import MCPToolClient
from agent.skill_loader import discover_skills, write_skill_usage
from agent.tool_registry import ToolRegistry
from client.public_api import PublicAPI, RequestBudgetExceeded
from detectors.common import iso, parse_time
from detectors.logs import PATTERN, detect_log_bursts
from detectors.metrics import detect_gauges
from detectors.traces import detect_trace_latency


@dataclass
class AgentOptions:
    case_id: str
    base_url: str
    question: str
    run_dir: Path
    project_root: Path
    config_path: Path
    skills_dir: Path | None = None
    mcp_config: Path | None = None
    request_budget: int = 55
    team_id: str = "starter-agent"
    agent_mode: str = "baseline"
    llm_env_file: Path | None = None
    competition_mode: str = "practice"
    team_token: str | None = None
    final_task_id: str | None = None


class CompetitionAgent:
    def __init__(self, options: AgentOptions):
        self.options = options
        self.options.run_dir.mkdir(parents=True, exist_ok=True)
        self.config = yaml.safe_load(options.config_path.read_text(encoding="utf-8"))
        self.log = self._logger()
        self.api = PublicAPI(
            options.base_url,
            options.run_dir,
            team_id=options.team_id,
            mode=options.competition_mode,
            final_task_id=options.final_task_id,
            team_token=options.team_token,
            request_budget=options.request_budget,
            timeout=float(self.config["api"]["timeout_seconds"]),
            max_retries=int(self.config["api"]["max_retries"]),
        )
        self.tools = ToolRegistry(self.api)
        self.debug: dict[str, Any] = {"states": [], "warnings": [], "question": options.question}

    def _logger(self) -> logging.Logger:
        logger = logging.getLogger(f"competition-agent-{self.options.run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        handler = logging.FileHandler(self.options.run_dir / "execution.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        return logger

    def state(self, name: str, detail: dict[str, Any] | None = None) -> None:
        entry = {"state": name, "detail": detail or {}}
        self.debug["states"].append(entry)
        self.log.info("state=%s detail=%s", name, json.dumps(detail or {}, ensure_ascii=False))

    def _windows(self, start: str, end: str) -> list[tuple[str, str]]:
        cursor, stop = parse_time(start), parse_time(end)
        width = timedelta(seconds=int(self.config["scan"]["window_seconds"]))
        windows = []
        while cursor < stop:
            right = min(cursor + width, stop)
            windows.append((iso(cursor), iso(right)))
            cursor = right
        return windows

    @staticmethod
    def _suspect_services(log_rows: list[dict[str, Any]], fallback: list[str], limit: int = 2) -> list[str]:
        counts: Counter[str] = Counter()
        for row in log_rows:
            severity = str(row.get("severity") or "").upper()
            if severity in {"ERROR", "FATAL", "CRITICAL"} or PATTERN.search(str(row.get("body") or "")):
                if row.get("service"):
                    counts[str(row["service"])] += 1
        selected = [name for name, _ in counts.most_common(limit)]
        for service in sorted(fallback):
            if service not in selected and len(selected) < limit:
                selected.append(service)
        return selected

    def collect(self) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        self.state("DISCOVER")
        cases = self.tools.call("list_cases").payload
        available = {x.get("case_id") for x in cases.get("items", cases.get("cases", [])) if isinstance(x, dict)}
        if available and self.options.case_id not in available:
            raise ValueError(f"unpublished or unknown case: {self.options.case_id}")
        metadata = self.tools.call("get_case_metadata", case_id=self.options.case_id).payload
        topology = self.tools.call("get_topology", case_id=self.options.case_id).payload
        self.state("PLAN", {"start": metadata["start_time"], "end": metadata["end_time"]})
        if self.options.competition_mode == "final":
            from agent.final_scout import scout
            metrics, logs, traces, candidates = scout(self, metadata, topology)
            self._final_scout_candidates = candidates
            self.debug["collection"] = {"windows": [(metadata["start_time"],metadata["end_time"])],
                "metric_rows":len(metrics),"log_rows":len(logs),"trace_rows":len(traces),
                "sampling_note":"complete families/operations plus labelled bounded discovery; see public_scout.json"}
            return metadata, metrics, logs, traces
        windows = self._windows(metadata["start_time"], metadata["end_time"])
        page_size = int(self.config["scan"]["page_size"])
        metric_rows: list[dict[str, Any]] = []
        log_rows: list[dict[str, Any]] = []
        self.state("COLLECT_METRICS_LOGS", {"windows": len(windows)})
        for window_index, (start, end) in enumerate(windows):
            metric_rows.extend(self.tools.call(
                "query_metrics", case_id=self.options.case_id,
                filters={"start": start, "end": end, "aggregation": "raw"},
                max_pages=1, page_size=page_size,
            ))
            # Alternate broad samples with targeted error searches. This keeps a
            # fixed request budget while avoiding a keyword-only view of logs.
            log_filters = {"start": start, "end": end}
            if window_index % 2 == 1:
                log_filters["keyword"] = self.config["scan"]["log_keyword"]
            log_rows.extend(self.tools.call(
                "query_logs", case_id=self.options.case_id, filters=log_filters,
                max_pages=1, page_size=page_size,
            ))
        services = list(metadata.get("public_services") or [])
        suspects = self._suspect_services(log_rows, services, int(self.config["scan"]["trace_service_limit"]))
        trace_rows: list[dict[str, Any]] = []
        self.state("COLLECT_TRACES", {"derived_suspects": suspects})
        for service in suspects:
            for start, end in windows:
                trace_rows.extend(self.tools.call(
                    "query_traces", case_id=self.options.case_id,
                    filters={"start": start, "end": end, "service": service},
                    max_pages=1, page_size=page_size,
                ))
        self.debug["collection"] = {
            "metric_rows": len(metric_rows), "log_rows": len(log_rows),
            "trace_rows": len(trace_rows), "trace_services": suspects,
            "windows": windows, "topology_kind": topology.get("kind"),
            "sampling_note": "one bounded page per time/entity slice; results are samples, not exhaustive modality totals",
            "log_sampling": "alternating broad pages and keyword-targeted pages",
            "tool_registry": self.tools.names(),
        }
        return metadata, metric_rows, log_rows, trace_rows

    def analyze(
        self,
        metadata: dict[str, Any],
        metrics: list[dict[str, Any]],
        logs: list[dict[str, Any]],
        traces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self.state("ANALYZE")
        if self.options.competition_mode == "final" and hasattr(self,"_final_scout_candidates"):
            write_skill_usage(self.options.run_dir / "skill_usage.json", {"discovered":[],"selected":[],"executed":[],
                "note":"Public scout used only gauge and same-operation trace statistics; no Counter was treated as a Gauge"})
            return self._final_scout_candidates
        metric_result = detect_gauges(metrics, float(self.config["detection"]["robust_z_threshold"]))
        skill_usage: dict[str, Any] = {
            "discovered": [], "selected": [], "executed": [],
            "counter_samples_withheld": metric_result["counter_samples_withheld"],
            "unknown_type_samples_withheld": metric_result["unknown_type_samples_withheld"],
        }
        candidates = list(metric_result["candidates"])
        for skill in discover_skills(self.options.skills_dir):
            skill_usage["discovered"].append({"name": skill.name, "path": str(skill.path), "handler": skill.handler})
            if skill.name == "counter-rate-analysis" and metric_result["counter_samples_withheld"]:
                skill_usage["selected"].append(skill.name)
                result = skill.load_handler()(
                    metrics,
                    z_threshold=float(self.config["detection"]["robust_z_threshold"]),
                    ratio_threshold=float(self.config["detection"]["counter_ratio_threshold"]),
                )
                candidates.extend(result["candidates"])
                skill_usage["executed"].append({"name": skill.name, "result": {k: v for k, v in result.items() if k != "candidates"}})
        write_skill_usage(self.options.run_dir / "skill_usage.json", skill_usage)
        candidates.extend(detect_log_bursts(logs, int(self.config["scan"]["window_seconds"])))
        candidates.extend(detect_trace_latency(traces, int(self.config["scan"]["window_seconds"])))
        self.debug["candidate_counts"] = dict(Counter(x["modality"] for x in candidates))
        self.debug["counter"] = {
            "withheld_without_skill": metric_result["counter_samples_withheld"],
            "unknown_type_withheld": metric_result["unknown_type_samples_withheld"],
            "misuse_count": metric_result["counter_misuse_count"],
        }
        return candidates

    def _mcp_enrich(self, candidates: list[dict[str, Any]]) -> None:
        if not self.options.mcp_config:
            return
        client = MCPToolClient(
            self.options.mcp_config, self.options.project_root, self.options.base_url,
            self.options.run_dir, self.options.competition_mode, self.options.team_token,
            self.options.final_task_id,
        )
        discovery = client.discover()
        self.debug["mcp_discovery"] = discovery
        trace_candidate = next(
            (x for x in candidates if x.get("modality") == "traces" and x.get("source_service")
             and x.get("target_service") and x.get("source_service") != x.get("target_service")),
            None,
        )
        if trace_candidate is None:
            trace_candidate = next(
                (x for x in candidates if x.get("modality") == "traces" and x.get("source_service") and x.get("target_service")),
                None,
            )
        if not trace_candidate:
            self.debug["warnings"].append("MCP available but no evidence-derived edge candidate was eligible")
            return
        width = int(self.config["scan"]["window_seconds"])
        analysis_start = iso(parse_time(trace_candidate["start_time"]).replace(microsecond=0))
        analysis_end = iso(parse_time(analysis_start) + timedelta(seconds=width))
        baseline_start = iso(parse_time(self.debug["collection"]["windows"][0][0]))
        baseline_end = iso(parse_time(baseline_start) + timedelta(seconds=width))
        arguments = {
            "case_id": self.options.case_id,
            "source_service": trace_candidate["source_service"],
            "target_service": trace_candidate["target_service"],
            "baseline_start": baseline_start,
            "baseline_end": baseline_end,
            "analysis_start": analysis_start,
            "analysis_end": analysis_end,
            "max_traces": int(self.config["mcp"]["max_traces"]),
        }
        result = client.call("analyze_edge_latency", arguments)
        trace_candidate["mcp_result"] = result
        if result.get("supported_conclusion") == "SUPPORTED":
            trace_candidate["score"] = float(trace_candidate.get("score", 0)) + 2
            trace_candidate["summary"] += "; MCP parent/child edge comparison supports the latency shift"

    def decide(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.state("VERIFY")
        self._mcp_enrich(candidates)
        trace_candidate = next(
            (x for x in sorted(candidates, key=lambda c: float(c.get("score", 0)), reverse=True)
             if x.get("modality") == "traces" and (x.get("evidence_records") or [{}])[0].get("trace_id")),
            None,
        )
        if trace_candidate:
            trace_id = trace_candidate["evidence_records"][0]["trace_id"]
            try:
                detail = self.tools.call("get_trace", case_id=self.options.case_id, trace_id=trace_id)
                spans = detail.payload.get("spans", detail.payload.get("items", []))
                trace_candidate["verification"] = {
                    "trace_id": trace_id, "span_count": len(spans),
                    "request_id": detail.request_id, "verified": bool(spans),
                }
            except Exception as exc:
                trace_candidate["verification"] = {"trace_id": trace_id, "verified": False, "error": str(exc)}
        candidates.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
        anomalies: list[dict[str, Any]] = []
        per_entity: Counter[str] = Counter()
        for candidate in candidates:
            records = candidate.get("evidence_records") or []
            if not records or not candidate.get("start_time") or not candidate.get("end_time"):
                continue
            entity = str(candidate.get("entity_id") or "unknown")
            if per_entity[entity] >= 2:
                continue
            evidence = [evidence_from_record(candidate["modality"], record) for record in records]
            mcp_result = candidate.get("mcp_result")
            anomalies.append(
                {
                    "entity_type": candidate["entity_type"],
                    "entity_id": entity,
                    "anomaly_type": self._semifinal_category(candidate),
                    "anomaly_description": self._anomaly_description(candidate),
                    "start_time": candidate["start_time"],
                    "end_time": candidate["end_time"],
                    "evidence": evidence,
                }
            )
            per_entity[entity] += 1
            if len(anomalies) >= int(self.config["output"]["max_anomalies"]):
                break
        return anomalies

    @staticmethod
    def _semifinal_category(candidate: dict[str, Any]) -> str:
        if candidate.get("published_anomaly_type") in {"MEMORY_USAGE_INCREASE","TRACE_LATENCY_INCREASE","REQUEST_ERROR_INCREASE","REQUEST_TIMEOUT_INCREASE"}:
            return candidate["published_anomaly_type"]
        detector = str(candidate.get("anomaly_type") or "")
        records = candidate.get("evidence_records") or []
        metric = str(candidate.get("metric_name") or (records[0].get("metric_name") if records else "") or "").lower()
        modality = str(candidate.get("modality") or "")
        if modality == "logs" or detector == "error_log_burst":
            return "LOG_ERROR_BURST"
        if modality == "traces":
            return "TRACE_LATENCY_INCREASE"
        if "memory" in metric:
            return "MEMORY_USAGE_INCREASE"
        if "cpu" in metric:
            return "CPU_USAGE_INCREASE"
        if "failed" in metric or "error" in metric:
            return "REQUEST_ERROR_INCREASE"
        if "restart" in metric:
            return "POD_RESTART_INCREASE"
        return "OTHER_OBSERVABLE_ANOMALY"

    @staticmethod
    def _anomaly_description(candidate: dict[str, Any]) -> str:
        summary = str(candidate.get("summary") or "").strip()
        entity = str(candidate.get("entity_id") or "unknown")
        if summary:
            return f"{entity} 在所标时间窗内出现可观测偏离：{summary}"[:1000]
        return f"{entity} 在所标时间窗内出现由真实 Public 证据支持的可观测异常。"

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            metadata, metrics, logs, traces = self.collect()
            candidates = self.analyze(metadata, metrics, logs, traces)
            anomalies = self.decide(candidates)
            answer = {
                "case_id": self.options.case_id,
                "schema_version": "3.0",
                "task_type": "SEMIFINAL_ANOMALY_DETECTION",
                "anomalies": anomalies,
            }
            schema = json.loads((self.options.project_root / "schemas/answer.schema.json").read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(answer)
            validation = validate_evidence(anomalies, self.options.run_dir)
            self.state("OUTPUT", {"anomalies": len(anomalies), "evidence_valid": validation["valid"]})
            (self.options.run_dir / "answer.json").write_text(
                json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.debug["requests_used"] = self.api.request_count
            self.debug["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.debug["status"] = "complete"
            (self.options.run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "complete", "agent_mode": "baseline", "case_id": self.options.case_id,
                        "model": None, "llm_calls": 0, "llm_tool_calls": 0,
                        "skill_executions": len(skill_usage.get("executed", [])) if 'skill_usage' in locals() else 0,
                        "mcp_calls": 0,
                        "anomaly_count": len(anomalies), "request_count": self.api.request_count,
                        "elapsed_seconds": self.debug["elapsed_seconds"],
                        "evidence_valid": validation["valid"],
                        "demo_public_scout_version": self.debug.get("public_scout",{}).get("version"),
                    },
                    ensure_ascii=False, indent=2,
                ), encoding="utf-8",
            )
            return answer
        except RequestBudgetExceeded as exc:
            self.debug["status"] = "budget_exhausted"
            self.debug["warnings"].append(str(exc))
            raise
        finally:
            self.api.close()
            (self.options.run_dir / "debug.json").write_text(
                json.dumps(self.debug, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
