import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cpu_safety_sampler import allocatable, classify


class CpuSafetyTests(unittest.TestCase):
    def test_allocatable_millicores(self):
        self.assertEqual(allocatable("7800m"), 7.8)
        self.assertEqual(allocatable("8"), 8)

    def test_headroom_or_psi_failure(self):
        self.assertTrue(classify(.8, .1, 8)["cpu_safety_ok"])
        self.assertFalse(classify(6.1, .1, 8)["cpu_safety_ok"])
        self.assertFalse(classify(.8, 5.1, 8)["cpu_safety_ok"])


if __name__ == "__main__":
    unittest.main()
