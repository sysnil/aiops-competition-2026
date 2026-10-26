"""Keep the pre-existing Image noise allowance separate from Cart controls."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sf004_admission import control_p95_limit


class IndependentControlCalibrationTest(unittest.TestCase):
    def test_image_uses_registered_absolute_clean_noise_tolerance(self):
        self.assertEqual(control_p95_limit(1.189, 1.25, 2.0), 3.189)
        self.assertLess(1.751, control_p95_limit(1.189, 1.25, 2.0))
        self.assertGreater(3.5, control_p95_limit(1.189, 1.25, 2.0))

    def test_cart_keeps_strict_relative_limit(self):
        self.assertAlmostEqual(control_p95_limit(17.737, 1.25), 22.17125)
        self.assertLess(21.107, control_p95_limit(17.737, 1.25))
        self.assertIsNone(control_p95_limit(None, 1.25, 2.0))


if __name__ == '__main__':
    unittest.main()
