from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analyze_controls import percentile, control_parent
from audit_traces import identity, missing_classification
from query_boundary_parents import classify
from audit_uid_canary import assess


class DiagnosticTests(unittest.TestCase):
    def test_small_sample_p95_is_fourth_slowest_at_sixty(self):
        values = list(range(1, 61))
        self.assertEqual(percentile(values, .95), 57)

    def test_control_parent_is_seed_and_request_specific(self):
        a = control_parent(861201, 101, 0)
        self.assertNotEqual(a, control_parent(861201, 101, 1))
        self.assertNotEqual(a, control_parent(861202, 101, 0))

    def test_exact_unexported_parent_vs_unknown(self):
        child = {"trace_id": "abcd", "phase": "before"}
        ref = ("CHILD_OF", "abcd", "1234")
        issued = {"abcd": {"external_parent_id": "1234"}}
        self.assertEqual(missing_classification(child, ref, {}, set(), issued),
                         "VERIFIED_UNEXPORTED_EXTERNAL_PARENT")
        self.assertEqual(missing_classification(child, ref, {}, set(), {}),
                         "UNKNOWN_UNRESOLVED_PARENT")

    def test_cross_phase_and_late_parent_are_separate(self):
        child = {"trace_id": "abcd", "phase": "during"}
        ref = ("CHILD_OF", "abcd", "1234")
        self.assertEqual(missing_classification(child, ref, {("abcd", "1234"):
            {"phase": "before"}}, set(), {}), "CROSS_PHASE_PARENT_FOUND")
        self.assertEqual(missing_classification(child, ref, {}, {("abcd", "1234")},
            {}), "PARENT_LATE_AFTER_PLUS120")

    def test_public_container_identity_is_inferred_not_direct(self):
        pod = {"uid": "uid", "service": "cart", "name": "cart-a"}
        maps = {"before": ({"uid": pod}, {"container": [pod]},
                           {"cart": [pod]})}
        missing = {"resource": {"container.id": "container"}, "service": "cart"}
        self.assertEqual(identity(missing, "before", maps),
            ("UID_UNIQUELY_INFERABLE_FROM_PUBLIC_TOPOLOGY", "container_id", "uid"))
        direct = {"resource": {"k8s.pod.uid": "uid"}, "service": "cart"}
        self.assertEqual(identity(direct, "before", maps)[0],
                         "UID_PRESENT_AND_VERIFIED")
        conflict = {"resource": {"k8s.pod.uid": "different"}, "service": "cart"}
        self.assertEqual(identity(conflict, "before", maps)[0], "UID_MISMATCH")

    def test_missing_multi_pod_uid_is_ambiguous(self):
        pods = [{"uid": "a", "service": "cart"}, {"uid": "b", "service": "cart"}]
        maps = {"before": ({}, {}, {"cart": pods})}
        self.assertEqual(identity({"resource": {}, "service": "cart"},
                                  "before", maps)[0], "UID_AMBIGUOUS")

    def test_parent_outside_exact_window_is_not_storage_loss(self):
        self.assertEqual(classify({"startTime": 99}, 100, 200),
                         "VERIFIED_PARENT_BEFORE_CAPTURE_WINDOW")
        self.assertEqual(classify({"startTime": 150}, 100, 200),
                         "FOUND_ONLY_AFTER_IMMUTABLE_AUDIT")
        self.assertEqual(classify(None, 100, 200),
                         "UNKNOWN_PARENT_NOT_IN_CURRENT_STORAGE")

    def test_uid_canary_rejects_missing_direct_uid(self):
        issued = {str(i) for i in range(4)}
        pods = {service: {"uid": service + "-uid", "name": service + "-pod"}
                for service in ("cart", "checkout", "shipping", "quote")}
        spans = {}
        for trace in issued:
            spans[(trace, "proxy")] = {"traceID": trace,
                "spanID": "proxy", "process": {"serviceName": "frontend-proxy",
                "tags": []}, "tag": {"span@kind": "server"}}
        for index, service in enumerate(pods):
            trace = str(index)
            spans[(trace, service)] = {"traceID": trace, "spanID": service,
                "operationName": service, "references": [],
                "process": {"serviceName": service, "tags": []},
                "tag": {"span@kind": "server"}}
        result = assess(spans, issued, pods, 1)
        self.assertFalse(result["checks"]["each_target_direct_uid_matches_pod"])
        self.assertTrue(result["checks"]["every_issued_trace_has_proxy_server_span"])


if __name__ == "__main__":
    unittest.main()
