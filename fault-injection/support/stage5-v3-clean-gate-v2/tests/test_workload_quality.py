import sys
from pathlib import Path
import tempfile
import json
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_clean_v2 import workload_quality


class WorkloadQualityTests(unittest.TestCase):
    def test_missing_replica_cannot_be_candidate_pass(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path / "route-proof.json").write_text(json.dumps({
                "status": "ROUTE_FLOW_PROOF_PASS", "rows": [{}] * 7}))
            analysis = {"pod_identity_by_phase": {"before": {
                "u1": {"service": "recommendation"}}},
                "service_server_trace_ids_by_pod_and_minute": {
                    "recommendation": {"u1": [40] * 25}},
                "call_edge_trace_ids_per_minute": {"checkout->cart": [40] * 25},
                "probes": {phase: {"planned": n, "valid_numeric": n,
                                    "max_consecutive_invalid": 0} for phase, n in
                           (("before", 120), ("during", 60), ("after", 120))},
                "state_machine_client": {"browse": [{"attempted": 1,
                                                      "successful": 1}]}}
            self.assertFalse(workload_quality(path, analysis)["all_pass"])
            analysis["pod_identity_by_phase"]["before"]["u2"] = {
                "service": "recommendation"}
            analysis["service_server_trace_ids_by_pod_and_minute"][
                "recommendation"]["u2"] = [40] * 25
            self.assertTrue(workload_quality(path, analysis)["all_pass"])


if __name__ == "__main__":
    unittest.main()
