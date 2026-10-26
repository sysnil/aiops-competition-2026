"""Static invariants for the third no-fault workload candidate."""

from pathlib import Path
import sys
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import fixed_control_load_c3


class CleanCandidateTests(unittest.TestCase):
    def test_c3_removes_redundant_control_and_preserves_quality_thresholds(self):
        c2 = yaml.safe_load((ROOT / "design/load-plan-c2.yaml").read_text())
        c3 = yaml.safe_load((ROOT / "design/load-plan-c3.yaml").read_text())
        c2_streams = {row["name"]: row for row in c2["workload"]["control_streams"]}
        c3_streams = {row["name"]: row for row in c3["workload"]["control_streams"]}
        self.assertEqual(set(c3_streams), {"image_asset", "cart_view"})
        self.assertEqual(c3_streams, {name: c2_streams[name] for name in c3_streams})
        self.assertEqual(set(fixed_control_load_c3.PATHS), set(c3_streams))
        self.assertEqual(c2["controls"], c3["controls"])
        self.assertEqual(c2["probe"], c3["probe"])
        self.assertEqual(c2["workload"]["max_added_node_cpu_cores"], 0.25)
        self.assertEqual(c3["controls"]["maximum_client_p95_ratio"], 1.25)
        profiles = yaml.safe_load((ROOT / "design/load-profiles-c3.yaml").read_text())
        self.assertEqual(profiles["profiles"]["MEDIUM"]["arrival_rate_rps"], 1.4)
        machine = yaml.safe_load((ROOT / "design/workload-state-machine-c3.yaml").read_text())
        self.assertIn("product", machine["operations"])
        self.assertIn("recommendations", machine["operations"])


if __name__ == "__main__":
    unittest.main()
