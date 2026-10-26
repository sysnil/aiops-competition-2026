import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_trace_v2 import classify_late


class TraceClassificationTests(unittest.TestCase):
    def test_known_eventstream_needs_no_issued_request_id(self):
        row = {"process": {"serviceName": "flagd", "tags": []},
               "operationName": "flagd.evaluation.v2.Service/EventStream"}
        traces = {("internal", "span"): ("sha", row)}
        self.assertEqual(classify_late(("internal", "span"), row, traces,
                                       {"ordinary_business": set()}, {}),
                         "VERIFIED_NONCRITICAL_CONTROL_PLANE")
        self.assertEqual(classify_late(("internal", "span"), row, traces,
                                       {"ordinary_business": {"internal"}}, {}),
                         "CRITICAL_BUSINESS_OR_CONTROL")

    def test_unknown_operation_or_mismatched_uid_fails_closed(self):
        row = {"process": {"serviceName": "recommendation", "tags": []},
               "operationName": "user-request"}
        traces = {("x", "y"): ("sha", row)}
        self.assertEqual(classify_late(("x", "y"), row, traces, {}, {}),
                         "UNCLASSIFIED_LATE")
        row = {"process": {"serviceName": "recommendation", "tags": [
            {"key": "k8s.pod.uid", "value": "wrong"}]},
            "operationName": "/flagd.evaluation.v2.Service/EventStream"}
        self.assertEqual(classify_late(("x", "y"), row, {("x", "y"): (
            "sha", row)}, {}, {}), "UNCLASSIFIED_LATE")


if __name__ == "__main__":
    unittest.main()
