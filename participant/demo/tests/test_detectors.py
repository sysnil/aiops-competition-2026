from __future__ import annotations

import unittest

from detectors.logs import detect_log_bursts
from detectors.traces import detect_trace_latency


class DetectorSafetyTests(unittest.TestCase):
    def test_missing_log_severity_is_still_analyzed(self) -> None:
        rows = [
            {"timestamp": f"2026-10-14T09:00:0{i}Z", "service": "svc", "body": "connection error"}
            for i in range(5)
        ]
        rows.append({"timestamp": "2026-10-14T09:05:00Z", "service": "svc", "body": "connection error"})
        self.assertTrue(detect_log_bursts(rows))

    def test_targeted_error_burst_needs_multiple_real_records(self) -> None:
        few = [
            {"timestamp": f"2026-10-14T09:00:0{i}Z", "service": "svc", "body": "connection error"}
            for i in range(4)
        ]
        enough = few + [
            {"timestamp": "2026-10-14T09:00:04Z", "service": "svc", "body": "connection error"}
        ]
        self.assertEqual([], detect_log_bursts(few))
        self.assertTrue(detect_log_bursts(enough))

    def test_ordinary_trace_presence_is_not_anomaly(self) -> None:
        rows = [
            {
                "start_time": f"2026-10-14T09:{i:02d}:00Z", "service": "svc",
                "duration_ms": 10 + (i % 2), "trace_id": str(i), "span_id": str(i),
                # status and pod UID intentionally absent
            }
            for i in range(15)
        ]
        self.assertEqual([], detect_trace_latency(rows))

    def test_extreme_error_trace_is_a_candidate_without_full_baseline(self) -> None:
        rows = [
            {
                "start_time": "2026-10-14T09:24:30Z",
                "service": "frontend",
                "parent_service": "frontend-proxy",
                "duration_ms": 120_000,
                "status": "ERROR",
                "trace_id": "a" * 32,
                "span_id": "b" * 16,
            }
        ]
        candidates = detect_trace_latency(rows)
        self.assertEqual(1, len(candidates))
        self.assertEqual("extreme_error_trace_latency", candidates[0]["anomaly_type"])


if __name__ == "__main__":
    unittest.main()
