from __future__ import annotations

import unittest
from datetime import timedelta

from qa_solver import QuestionSolver, bucket_labels, bucket_start, nearest_rank_percentile, parse_utc


class StarterQASolverTests(unittest.TestCase):
    def setUp(self):
        self.solver = QuestionSolver.__new__(QuestionSolver)
        self.series = [{
            "name": "service-a",
            "unit": "ms",
            "points": [
                {"timestamp": "2026-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2026-01-01T00:01:00Z", "value": 3.0},
                {"timestamp": "2026-01-01T00:02:00Z", "value": 2.0},
            ],
        }]

    def answer(self, kind: str, source_kind: str = "memory"):
        return self.solver._answer(
            {"index": 1, "calculation": {"type": kind, "unit": "ms", "source_kind": source_kind}},
            self.series,
            [],
        )

    def test_case_origin_buckets_and_nearest_rank(self):
        origin = parse_utc("2026-01-01T00:00:25Z")
        self.assertEqual(bucket_start("2026-01-01T00:01:24.999999Z", origin, 60), origin)
        self.assertEqual(bucket_start("2026-01-01T00:01:25Z", origin, 60), origin + timedelta(seconds=60))
        self.assertEqual(len(bucket_labels(origin, origin + timedelta(minutes=3), origin, 60)), 3)
        self.assertEqual(nearest_rank_percentile([1.0, 2.0, 3.0, 100.0]), 100.0)

    def test_implemented_single_series_answers(self):
        self.assertEqual(self.answer("max_value")["value"], 3.0)
        self.assertEqual(self.answer("max_time")["value"], "2026-01-01T00:01:00+00:00")
        self.assertEqual(self.answer("mean")["value"], 2.0)

    def test_unfinished_cpu_is_explicit_not_fabricated(self):
        result = self.answer("mean", "cpu_rate")
        self.assertEqual(result["value"], "NOT_IMPLEMENTED:TODO-02")

    def test_unfinished_multi_series_is_explicit(self):
        result = self.answer("higher_mean_series", "memory")
        self.assertEqual(result["value"], "NOT_IMPLEMENTED:TODO-03")

    def test_unfinished_window_comparisons_are_classified(self):
        server = self.answer("range_mean_difference", "server_p95")
        edge = self.answer("range_mean_difference", "edge_p95")
        self.assertEqual(server["value"], "NOT_IMPLEMENTED:TODO-04")
        self.assertEqual(edge["value"], "NOT_IMPLEMENTED:TODO-05")

    def test_public_chart_adapter_keeps_safe_basic_labels(self):
        raw = {
            "question_id": "SYN-01", "case_id": "SYNTHETIC", "title": "Synthetic memory",
            "modality": "Metrics", "target_entity_type": "Pod", "target_entity_id": "pod-a",
            "time_range": {"start_utc": "2026-01-01T00:00:25Z", "end_exclusive_utc": "2026-01-01T00:03:25Z"},
            "query_spec": {
                "kind": "memory", "answer_plan": "basic_mean",
                "filters": {"entities": ["pod-a"], "metric_name": "memory_bytes"},
                "time_bucket": {"origin_utc": "2026-01-01T00:00:25Z", "width_seconds": 60},
                "minimum_samples_per_minute": 1, "missing_policy": "null",
                "counter_reset_policy": "not applicable", "p95_definition": "not applicable",
            },
            "chart_requirement": {"type": "line", "x_axis": "UTC bucket start", "y_axis": "MiB", "series": ["pod-a"], "legend_required": False},
            "questions": [{"text": "max"}, {"text": "time"}, {"text": "mean"}],
        }
        normalized = self.solver._normalize_question(raw)
        self.assertEqual(normalized["chart_requirement"]["x_axis"]["label"], "Time")
        self.assertEqual(normalized["chart_requirement"]["y_axis"]["label"], "Value")


if __name__ == "__main__":
    unittest.main()
