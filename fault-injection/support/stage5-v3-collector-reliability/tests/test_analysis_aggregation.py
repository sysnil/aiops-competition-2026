import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from scripts import qualify_network_controls, summarize_continuous


class AnalysisAggregationTests(unittest.TestCase):
    def test_preregistered_minute_stability_rejects_low_count(self):
        minutes = [{"minute": index, "image_success": 60,
            "image_edge_spans": 60, "image_client_latency_ms": {"p95": 3.0}}
            for index in range(45)]
        minutes[20]["image_edge_spans"] = 29
        rule = {"minimum_successful_requests_per_minute": 30,
            "minimum_valid_client_server_edges_per_minute": 30,
            "p95_ratio_against_first_ten_minute_median_max": 1.25}
        value = qualify_network_controls.stability(minutes, "image", "image_success",
            "image_edge_spans", "image_client_latency_ms", rule)
        self.assertFalse(value["all_45_minutes_count_pass"])
        self.assertEqual(value["count_failure_minutes"], [20])

    def test_low_baseline_p95_ratio_keeps_absolute_change_visible(self):
        minutes = [{"minute": index, "image_success": 60,
            "image_edge_spans": 60,
            "image_client_latency_ms": {"p95": 3.0 if index < 10 else 4.0}}
            for index in range(45)]
        rule = {"minimum_successful_requests_per_minute": 30,
            "minimum_valid_client_server_edges_per_minute": 30,
            "p95_ratio_against_first_ten_minute_median_max": 1.25}
        value = qualify_network_controls.stability(minutes, "image", "image_success",
            "image_edge_spans", "image_client_latency_ms", rule)
        self.assertFalse(value["all_45_minutes_latency_pass"])
        self.assertEqual(value["minute_rows"][10]["p95_absolute_change_ms"], 1.0)

    def test_missing_limiter_series_cannot_produce_reliability_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            prereg = root / "prereg.yaml"
            prereg.write_text(yaml.safe_dump({"id": "R", "workload": {
                "duration_seconds": 2700}}))
            (run / "schedule.json").write_text(json.dumps({"id": "R",
                "scheduled_start_utc": "2026-10-12T00:00:00+00:00",
                "scheduled_end_utc": "2026-10-12T00:45:00+00:00"}))
            (run / "result.json").write_text(json.dumps({"status":
                "CONTINUOUS_DIAGNOSTIC_WORKLOAD_COMPLETED"}))
            names = ("receiver_accepted_grpc", "receiver_refused_grpc",
                "limiter_refused_spans", "jaeger_sent_spans", "jaeger_queue_size",
                "heap_bytes", "working_set_bytes", "cpu_cores")
            row = {"metrics": {name: {"value": None if name ==
                "limiter_refused_spans" else 0, "status": "SERIES_ABSENT_UNKNOWN_NOT_ZERO"
                if name == "limiter_refused_spans" else "PRESENT"} for name in names}}
            (run / "monitor.jsonl").write_text((json.dumps(row) + "\n") * 180)
            summary = {"issued_requests": 100, "proxy_server_matched": 100,
                "missing_proxy_trace_ids": [], "missing_control_backend": {
                    "image_asset": [], "cart_view": []}, "missing_cart_frontend": [],
                "uid_counts": {name: {"server_spans": 40,
                    "direct_uid_verified": 40, "missing_uid": 0, "mismatched_uid": 0}
                    for name in ("cart", "checkout", "shipping", "quote")},
                "failed_request_rows": 0, "request_window_errors": []}
            first = root / "first.json"
            late = root / "late.json"
            for path in (first, late):
                path.write_text(json.dumps({"status": "TRACE_CAPTURE_COMPLETE",
                    "summary": summary}))
            comparison = root / "comparison.json"
            comparison.write_text(json.dumps({"status": "CONTINUOUS_TRACE_COMPLETION_PASS"}))
            proofs = []
            for number in ("2", "3"):
                path = root / ("proof" + number + ".json")
                path.write_text(json.dumps({"worker_ip": "192.168.53.5" +
                    ("4" if number == "2" else "5"),
                    "status": "CONTINUOUS_ARCHIVE_VERIFIED_AND_RESTORED",
                    "source_manifest_sha256": "same"}))
                proofs.append(path)
            args = ["summarize_continuous.py", "--prereg", str(prereg), "--run", str(run),
                "--first", str(first), "--late", str(late), "--comparison", str(comparison),
                "--archive-proof", str(proofs[0]), "--archive-proof", str(proofs[1]),
                "--output", str(root / "out.json")]
            with patch("sys.argv", args):
                summarize_continuous.main()
            result = json.loads((root / "out.json").read_text())
            self.assertEqual(result["status"], "RELIABILITY_INCONCLUSIVE")
            self.assertTrue(result["limiter_counter_absent_not_zero"])


if __name__ == "__main__":
    unittest.main()
