import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/timed_trace_capture.py"
sys.path.insert(0, str(SOURCE.parents[2] / "stage5-v3-collector-reliability" / "scripts"))
spec = importlib.util.spec_from_file_location("timed_trace_capture", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TraceScheduleTests(unittest.TestCase):
    def test_registered_offsets(self):
        self.assertEqual([row[1] for row in module.STAGES], [5, 60, 120, 240])

    def test_on_time_stage(self):
        row = module.check_stage_timing(120, 125, 180, 1121, 1150, 1000)
        self.assertEqual(row["completion_lag_seconds"], 150)

    def test_late_start_fails_closed(self):
        with self.assertRaises(ValueError):
            module.check_stage_timing(120, 125, 180, 1126, 1140, 1000)

    def test_late_completion_fails_closed(self):
        with self.assertRaises(ValueError):
            module.check_stage_timing(240, 245, 300, 1241, 1301, 1000)

    def test_synthetic_issued_to_proxy_and_uid(self):
        rows = [{"stream": "image_asset", "trace_id": "a" * 32,
                 "time_utc": datetime.fromtimestamp(1, timezone.utc).isoformat(),
                 "latency_ms": 10, "success": True},
                {"stream": "cart_view", "trace_id": "b" * 32,
                 "time_utc": datetime.fromtimestamp(2, timezone.utc).isoformat(),
                 "latency_ms": 10, "success": True}]

        def span(trace, service, uid=None):
            return {"traceID": trace, "tag": {"span@kind": "server"},
                "process": {"serviceName": service,
                    "tags": [{"key": "k8s.pod.uid", "value": uid}] if uid else []}}

        spans = [span("a" * 32, "frontend-proxy"),
                 span("b" * 32, "frontend-proxy"),
                 span("b" * 32, "frontend"),
                 span("a" * 32, "image-provider"),
                 span("b" * 32, "cart", "cart-uid")]
        uids = {service: [service + "-uid"] for service in module.TARGETS}
        uids["cart"] = ["cart-uid"]
        result = module.summarize(rows, spans, uids, 0)
        self.assertEqual(result["proxy_server_matched"], 2)
        self.assertEqual(result["fixed_image_backend_missing"], [])
        self.assertEqual(result["fixed_cart_backend_missing"], [])
        self.assertEqual(result["uid_counts"]["cart"]["direct_uid_verified"], 1)

    def test_synthetic_missing_uid_not_filled_from_gt(self):
        rows = [{"stream": "cart_view", "trace_id": "b" * 32,
                 "time_utc": datetime.fromtimestamp(2, timezone.utc).isoformat(),
                 "success": True}]
        spans = [{"traceID": "b" * 32, "tag": {"span@kind": "server"},
                  "process": {"serviceName": "cart", "tags": []}}]
        uids = {service: [service + "-uid"] for service in module.TARGETS}
        result = module.summarize(rows, spans, uids, 0)
        self.assertEqual(result["uid_counts"]["cart"]["missing_uid"], 1)
        self.assertEqual(result["missing_proxy"], ["b" * 32])


if __name__ == "__main__":
    unittest.main()
