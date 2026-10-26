import importlib.util
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/sf003_admission.py'
SPEC = importlib.util.spec_from_file_location('sf003_admission', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PodMemoryEvidence(unittest.TestCase):
    def test_four_regular_samples_form_a_direct_signal(self):
        self.assertEqual(MODULE.longest_consecutive([600, 615, 630, 645]), 4)

    def test_missing_window_breaks_consecutive_evidence(self):
        self.assertEqual(MODULE.longest_consecutive([600, 615, 660, 675]), 2)

    def test_reordered_or_duplicate_samples_cannot_extend_a_run(self):
        self.assertEqual(MODULE.longest_consecutive([630, 600, 615, 615]), 3)


if __name__ == '__main__':
    unittest.main()
