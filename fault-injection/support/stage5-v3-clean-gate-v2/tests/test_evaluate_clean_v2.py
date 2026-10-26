import sys
from pathlib import Path
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from evaluate_clean_v2 import decide_control


POLICY = yaml.safe_load((Path(__file__).resolve().parents[1] /
                         "design/clean-gate-v2-proposed.yaml").read_text())


class CleanV2Tests(unittest.TestCase):
    def test_single_mild_excursion_only_candidate_passes(self):
        ratios = [1.0] * 15
        ratios[4] = 1.26
        value = decide_control(ratios, 1.03, 0, 60, 59, POLICY)
        self.assertFalse(value["A_old"])
        self.assertTrue(value["B_persistence_peak"])
        self.assertTrue(value["E_B_and_window"])

    def test_severe_short_excursion_and_consecutive_excursions_fail(self):
        severe = [1.0] * 15
        severe[3] = 1.69
        self.assertFalse(decide_control(severe, 1.0, 0, 60, 59, POLICY)[
            "B_persistence_peak"])
        repeated = [1.0] * 15
        repeated[3:5] = [1.3, 1.3]
        self.assertFalse(decide_control(repeated, 1.0, 0, 60, 59, POLICY)[
            "B_persistence_peak"])

    def test_failure_missing_span_and_missing_minute_fail(self):
        ratios = [1.0] * 15
        self.assertFalse(decide_control(ratios, 1.0, 1, 60, 59, POLICY)[
            "E_B_and_window"])
        self.assertFalse(decide_control(ratios, 1.0, 0, 60, 29, POLICY)[
            "E_B_and_window"])
        ratios[2] = None
        self.assertFalse(decide_control(ratios, 1.0, 0, 60, 59, POLICY)[
            "E_B_and_window"])


if __name__ == "__main__":
    unittest.main()
