import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'scripts/timeline_candidates.py'
SPEC = importlib.util.spec_from_file_location('timeline_candidates', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CandidateBounds(unittest.TestCase):
    def test_all_candidate_windows_fit_without_overlap(self):
        for n in range(2, 7):
            plan = MODULE.propose(n)
            self.assertLessEqual(plan['complete_by_seconds'], 1800)
            self.assertEqual(len(plan['starts_seconds']), n)
            self.assertTrue(plan['requires_deferred_full_chunk_audit'])
            self.assertFalse(plan['execution_authorized'])

    def test_current_two_300s_envelope_exceeds_window(self):
        earliest = 720 + 300 + 240 + 45 + 105 + 300 + 180 + 45
        self.assertEqual(earliest, 1935)
        self.assertGreater(earliest, 1800)

    def test_unchanged_300s_mechanisms_conflict_with_shorter_patterns(self):
        for n in range(3, 7):
            self.assertLess(MODULE.PATTERNS[n]['duration'], MODULE.KNOWN_DURATION['ServicePause'])
            self.assertLess(MODULE.PATTERNS[n]['duration'], MODULE.KNOWN_DURATION['PodMemoryStress'])


if __name__ == '__main__':
    unittest.main()
