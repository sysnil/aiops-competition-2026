import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/assess_clean_budget.py"
spec = importlib.util.spec_from_file_location("assess_clean_budget", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CleanBudgetTests(unittest.TestCase):
    def test_prior_window_exceeds_floor(self):
        value = module.decide(46_200_000_000, 46_000_000_000,
            [4_777_852_529, 4_788_885_438], "GLOBAL_SAFETY_PASS",
            "OBSERVABILITY_SMOKE_PASS")
        self.assertEqual(value["status"], "CLEAN_ENGINEERING_BLOCKED")
        self.assertFalse(value["storage_preflight_pass"])

    def test_safety_failure_cannot_start_even_with_storage(self):
        value = module.decide(55_000_000_000, 46_000_000_000,
            [4_777_852_529, 4_788_885_438], "GLOBAL_SAFETY_FAIL",
            "OBSERVABILITY_SMOKE_PASS")
        self.assertEqual(value["status"], "CLEAN_STORAGE_PASS_FRESH_HEALTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
