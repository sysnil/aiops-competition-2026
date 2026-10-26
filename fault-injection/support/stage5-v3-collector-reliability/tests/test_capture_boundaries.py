import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import audit_short_canary, run_continuous


class CaptureBoundaryTests(unittest.TestCase):
    def test_trace_id_collision_is_bounded_to_current_window(self):
        calls = []

        def fake_request(url, body=None):
            calls.append((url, body))
            if url.endswith("/_settings"):
                return {"index": {"settings": {"index": {"uuid": "same"}}}}
            return {"_shards": {"failed": 0}, "hits": {"total": {"relation": "eq", "value": 0},
                    "hits": []}, "_scroll_id": None}

        with tempfile.TemporaryDirectory() as temp, patch.object(
                audit_short_canary, "request_json", side_effect=fake_request):
            _, source = audit_short_canary.capture("index", ["old-id"],
                Path(temp), 1000, 2000)
        self.assertEqual(source["query_start_us"], 1000)
        self.assertIn({"range": {"startTime": {"gte": 1000, "lt": 2000}}},
                      calls[1][1]["query"]["bool"]["filter"])

    def test_missing_collector_metric_fails_closed(self):
        with patch.object(run_continuous, "metric", side_effect=RuntimeError("series absent")):
            with self.assertRaisesRegex(RuntimeError, "series absent"):
                run_continuous.monitor_row({"pod_name": "collector", "pod_uid": "uid"},
                    None, 1, "hash", {})

    def test_refused_counter_increase_fails_window(self):
        def fake_metric(expression, optional=False):
            value = 2 if "receiver_refused_spans_total" in expression else 1
            return {"value": value, "sample_timestamp": 1, "labels": {}}

        previous = {"receiver_accepted_grpc": 1, "receiver_refused_grpc": 1,
                    "limiter_refused_spans": 1, "jaeger_sent_spans": 1}
        with patch.object(run_continuous, "metric", side_effect=fake_metric):
            with self.assertRaisesRegex(RuntimeError, "Collector refusal"):
                run_continuous.monitor_row({"pod_name": "collector", "pod_uid": "uid"},
                    previous, 1, "hash", {})

    def test_lazy_limiter_counter_absence_stays_unknown(self):
        def fake_metric(expression, optional=False):
            if "memory_limiter_refused_spans_total" in expression:
                self.assertTrue(optional)
                return {"value": None, "sample_timestamp": None, "labels": {},
                        "status": "SERIES_ABSENT_UNKNOWN_NOT_ZERO"}
            return {"value": 0, "sample_timestamp": 1, "labels": {}, "status": "PRESENT"}

        previous = {"receiver_accepted_grpc": 0, "receiver_refused_grpc": 0,
                    "limiter_refused_spans": None, "jaeger_sent_spans": 0}
        with patch.object(run_continuous, "metric", side_effect=fake_metric):
            row, counters = run_continuous.monitor_row({"pod_name": "collector",
                "pod_uid": "uid"}, previous, 1, "hash", {})
        self.assertIsNone(counters["limiter_refused_spans"])
        self.assertEqual(row["metrics"]["limiter_refused_spans"]["status"],
                         "SERIES_ABSENT_UNKNOWN_NOT_ZERO")


if __name__ == "__main__":
    unittest.main()
