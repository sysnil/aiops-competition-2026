from __future__ import annotations

import unittest
import time
from types import SimpleNamespace

from agent.llm_core import LLMCompetitionAgent, function_tool
from detectors.metrics import detect_gauges, metric_kind


class PublicRecoveryTests(unittest.TestCase):
    def test_metric_kind_uses_only_known_public_name_semantics(self) -> None:
        memory = {"metric_name": "container_memory_working_set_bytes", "metric_type": None, "value": 10}
        self.assertEqual("gauge", metric_kind(memory))
        self.assertEqual("counter", metric_kind({"metric_name": "container_cpu_usage_seconds_total"}))
        self.assertEqual("", metric_kind({"metric_name": "unverified_bytes"}))
        self.assertEqual("", metric_kind({"metric_name": "looks_total"}))
        self.assertEqual("gauge", metric_kind({"metric_name": "calls_total", "metric_type": "gauge"}))
        detect_gauges([memory])
        self.assertIsNone(memory["metric_type"])

    def test_gauge_candidate_retains_public_pod_identity(self) -> None:
        rows = [{
            "timestamp": f"2026-10-17T07:00:{index:02d}Z",
            "metric_name": "container_memory_working_set_bytes",
            "metric_type": None,
            "pod": "frontend-abc",
            "value": value,
            "labels": {},
        } for index, value in enumerate([10, 10, 10, 10, 10, 100])]
        candidates = detect_gauges(rows)["candidates"]
        self.assertTrue(candidates)
        self.assertEqual("Pod", candidates[0]["entity_type"])
        self.assertEqual("frontend-abc", candidates[0]["entity_id"])

    def test_recovery_samples_each_window_and_keeps_raw_evidence(self) -> None:
        agent = object.__new__(LLMCompetitionAgent)
        agent.public_window = ("2026-10-17T07:00:00Z", "2026-10-17T07:10:00Z")
        agent.recovery_attempted = False
        agent.api = SimpleNamespace(request_budget=50, request_count=0)
        agent.config = {
            "scan": {"window_seconds": 300, "page_size": 20, "log_keyword": "error"},
            "detection": {"robust_z_threshold": 5.0, "counter_ratio_threshold": 2.0},
        }
        agent.options = SimpleNamespace(case_id="SF-006")
        agent.cache = {"metrics": [], "logs": [], "traces": []}
        agent.skills = {}
        agent.candidates = {}
        agent._candidate_keys = {}
        seen = []
        events = []

        def query(name, **kwargs):
            seen.append((name, kwargs["filters"].copy()))
            return []

        agent.tools = SimpleNamespace(call=query)
        agent.state = lambda name, detail: events.append((name, detail))
        agent._recover_public_candidates()
        self.assertEqual(6, len(seen))
        self.assertEqual(2, len({filters["start"] for _, filters in seen}))
        self.assertEqual([None, "error"], [filters.get("keyword") for name, filters in seen if name == "query_logs"])
        self.assertTrue(agent.recovery_attempted)
        self.assertEqual("LLM_PUBLIC_RECOVERY", events[-1][0])

    def test_fallback_requires_a_verified_candidate(self) -> None:
        agent = object.__new__(LLMCompetitionAgent)
        agent.options = SimpleNamespace(competition_mode="final")
        agent.final_specs = []
        agent.candidates = {}
        agent.taxonomy = {"LOG_ERROR_BURST"}
        agent.taxonomy_allowed_entities = {"LOG_ERROR_BURST": {"Service"}}
        agent.public_services = {"checkout"}
        agent.public_pod_services = {}
        agent.recovery_selected = None
        agent.state = lambda name, detail: None
        agent._select_public_fallback()
        self.assertEqual([], agent.final_specs)
        agent.candidates = {"cand-001": {
            "modality": "logs", "entity_type": "Service", "entity_id": "checkout",
            "score": 6.0, "summary": "five real error logs",
            "evidence_records": [{"timestamp": "2026-10-17T07:00:00Z"}],
        }}
        agent._select_public_fallback()
        self.assertEqual("cand-001", agent.recovery_selected)
        self.assertEqual("LOG_ERROR_BURST", agent.final_specs[0]["anomaly_type"])

    def test_final_tool_refusal_uses_recorded_public_candidate(self) -> None:
        agent = object.__new__(LLMCompetitionAgent)
        agent.options = SimpleNamespace(competition_mode="final", question="Investigate anomalies", case_id="SF-006")
        agent.public_window = None
        agent.recovery_attempted = False
        agent.max_rounds = 1
        agent.total_timeout_seconds = 60
        agent.max_tool_calls = 10
        agent.tool_call_count = 0
        agent.analysis_call_count = 3
        agent.skill_execution_count = 0
        agent.mcp_call_count = 0
        agent.skills = {}
        agent.mcp = None
        agent.cache = {"metrics": [], "logs": [], "traces": []}
        agent.candidates = {"cand-001": {
            "modality": "logs", "entity_type": "Service", "entity_id": "checkout",
            "score": 6.0, "summary": "five real error logs",
            "evidence_records": [{"timestamp": "2026-10-17T07:00:00Z"}],
        }}
        agent.taxonomy = {"LOG_ERROR_BURST"}
        agent.taxonomy_allowed_entities = {"LOG_ERROR_BURST": {"Service"}}
        agent.public_services = {"checkout"}
        agent.public_pod_services = {}
        agent.final_specs = None
        agent.recovery_selected = None
        choices = []
        agent.llm = SimpleNamespace(
            config=SimpleNamespace(max_output_tokens=1200),
            chat=lambda *args, **kwargs: choices.append(kwargs["tool_choice"]) or {"message": {"content": "no tool call"}},
        )
        events = []
        agent.state = lambda name, detail: events.append(name)
        agent._native_loop([], [function_tool("finalize_answer", "Finish", {})], time.monotonic())
        self.assertEqual([], agent.final_specs)
        self.assertIn("LLM_FINALIZE_TOOL_IGNORED", events)
        self.assertEqual("required", choices[0])
        agent._select_public_fallback()
        self.assertEqual("cand-001", agent.recovery_selected)

    def test_trace_evidence_outranks_unbounded_counter_score(self) -> None:
        agent = object.__new__(LLMCompetitionAgent)
        agent.options = SimpleNamespace(competition_mode="final")
        agent.final_specs = []
        agent.public_services = {"recommendation", "frontend-proxy"}
        agent.public_pod_services = {}
        agent.taxonomy = {"TRACE_LATENCY_INCREASE", "OTHER_OBSERVABLE_ANOMALY"}
        agent.taxonomy_allowed_entities = {}
        agent.recovery_selected = None
        agent.state = lambda name, detail: None
        agent.candidates = {
            "cand-001": {"modality": "metrics", "entity_type": "Service", "entity_id": "frontend-proxy",
                         "score": 1e10, "evidence_records": [{"timestamp": "2026-10-17T07:00:00Z"}]},
            "cand-002": {"modality": "traces", "entity_type": "Service", "entity_id": "recommendation",
                         "score": 5.0, "evidence_records": [{"start_time": "2026-10-17T07:05:00Z"}]},
        }
        agent._select_public_fallback()
        self.assertEqual("cand-002", agent.final_specs[0]["candidate_id"])


if __name__ == "__main__":
    unittest.main()
