from datetime import datetime, timezone
import unittest

from scripts.capture_continuous_traces import summarize


class ContinuousSummaryTests(unittest.TestCase):
    def test_issued_denominator_and_direct_uid(self):
        start = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
        request = {"stream": "cart_view", "trace_id": "trace-one", "success": True,
            "time_utc": "2026-10-12T15:00:00.100000+00:00", "latency_ms": 4,
            "status_code": 200}
        spans = [
            {"traceID": "trace-one", "spanID": "p", "process": {
                "serviceName": "frontend-proxy", "tags": []},
                "tag": {"span@kind": "server"}, "duration": 3000},
            {"traceID": "trace-one", "spanID": "f", "process": {
                "serviceName": "frontend", "tags": [{"key": "k8s.pod.uid", "value": "frontend-uid"}]},
                "tag": {"span@kind": "server"}, "duration": 2000},
            {"traceID": "trace-one", "spanID": "fc", "process": {
                "serviceName": "frontend", "tags": [{"key": "k8s.pod.uid", "value": "frontend-uid"}]},
                "tag": {"span@kind": "client"}, "duration": 1000},
            {"traceID": "trace-one", "spanID": "c", "process": {
                "serviceName": "cart", "tags": [{"key": "k8s.pod.uid", "value": "cart-uid"}]},
                "tag": {"span@kind": "server"}, "duration": 800,
                "references": [{"traceID": "trace-one", "spanID": "fc"}]},
        ]
        schedule = {"scheduled_start_utc": start.isoformat(),
            "frontend_worker1_uid": "frontend-uid",
            "initial_infrastructure": {"business_pod_uids": {
                "cart": ["cart-uid"], "checkout": ["checkout-uid"],
                "shipping": ["shipping-uid"], "quote": ["quote-uid"]}}}
        summary = summarize([request], spans, schedule)
        self.assertEqual(summary["issued_requests"], 1)
        self.assertEqual(summary["proxy_server_matched"], 1)
        self.assertEqual(summary["uid_counts"]["cart"]["direct_uid_verified"], 1)
        self.assertEqual(summary["per_minute"][0]["cart_edge_spans"], 1)
        self.assertEqual(summary["per_minute"][0]["cart_success"], 1)

    def test_real_issued_request_missing_proxy_not_discarded(self):
        schedule = {"scheduled_start_utc": "2026-10-12T15:00:00+00:00",
            "frontend_worker1_uid": "uid", "initial_infrastructure": {
                "business_pod_uids": {name: ["uid"] for name in
                    ("cart", "checkout", "shipping", "quote")}}}
        row = {"stream": "image_asset", "trace_id": "sent-id", "success": True,
               "time_utc": "2026-10-12T15:00:00.010000+00:00", "latency_ms": 2}
        summary = summarize([row], [], schedule)
        self.assertEqual(summary["issued_requests"], 1)
        self.assertEqual(summary["missing_proxy_trace_ids"], ["sent-id"])
        self.assertEqual(summary["per_minute"][0]["image_issued"], 1)


if __name__ == "__main__":
    unittest.main()
