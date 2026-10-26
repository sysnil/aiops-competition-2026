from __future__ import annotations

import unittest

from detectors.metrics import detect_counter_rates, detect_gauges
from agent.skill_loader import discover_skills
from pathlib import Path


class CounterSkillTests(unittest.TestCase):
    def test_counter_is_not_treated_as_gauge_and_reset_is_skipped(self) -> None:
        values = [0, 10, None, 30, 5, 15, 25]
        rows = []
        for index, value in enumerate(values):
            rows.append(
                {
                    "timestamp": f"2026-10-14T09:{index:02d}:00Z",
                    "metric_name": "requests_total",
                    "value": value,
                    "metric_type": "counter",
                    "service": "svc-a",
                    "labels": {"route": "/"},
                    "_request_id": "req-0001",
                }
            )
        baseline = detect_gauges(rows)
        self.assertEqual([], baseline["candidates"])
        self.assertEqual(len(rows), baseline["counter_samples_withheld"])
        result = detect_counter_rates(rows, z_threshold=99)
        self.assertEqual(1, result["reset_count"])
        self.assertGreater(result["invalid_interval_count"], 0)
        self.assertGreater(result["derived_rates"], 0)

    def test_unknown_metric_type_is_not_inferred_as_gauge(self) -> None:
        rows = [{
            "timestamp": f"2026-10-14T09:0{i}:00Z", "metric_name": "looks_total",
            "value": 1000 + i * 100, "metric_type": None, "service": "svc", "labels": {},
        } for i in range(6)]
        result = detect_gauges(rows)
        self.assertEqual([], result["candidates"])
        self.assertEqual(6, result["unknown_type_samples_withheld"])

    def test_counter_candidate_keeps_pod_identity(self) -> None:
        values = [0, 1, 2, 3, 100, 101]
        rows = [{
            "timestamp": f"2026-10-14T09:00:{index:02d}Z",
            "metric_name": "container_cpu_usage_seconds_total",
            "value": value,
            "metric_type": "counter",
            "pod": "workload-abc",
            "labels": {},
        } for index, value in enumerate(values)]
        candidates = detect_counter_rates(rows, z_threshold=2, ratio_threshold=2)["candidates"]
        self.assertTrue(candidates)
        self.assertEqual("Pod", candidates[0]["entity_type"])
        self.assertEqual("workload-abc", candidates[0]["entity_id"])

    def test_skill_is_discovered_and_handler_is_callable(self) -> None:
        root = Path(__file__).resolve().parents[1]
        skills = discover_skills(root / "skills")
        self.assertIn("counter-rate-analysis", {x.name for x in skills})
        self.assertTrue(callable(skills[0].load_handler()))


if __name__ == "__main__":
    unittest.main()
