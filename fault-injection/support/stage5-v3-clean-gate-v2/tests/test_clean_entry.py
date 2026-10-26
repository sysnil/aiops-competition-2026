from datetime import datetime, timezone
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from start_clean import valid_entry


class CleanEntryTests(unittest.TestCase):
    def test_failing_or_stale_safety_refuses_clean(self):
        now = datetime.now(timezone.utc).isoformat()
        checks = {"all": True}
        prereg = {"status": "PREREGISTERED_BEFORE_VALIDATION",
                  "validation": {"runs": [{"id": "S5V3AP-CGV2-VAL-1"}],
                  "master_storage_before_each_run_min_bytes": 46_000_000_000}}
        decision = {"status": "GLOBAL_SAFETY_PASS", "faults_allowed": True,
                    "checks": checks, "captured_at_utc": now}
        self.assertTrue(valid_entry(decision, prereg, "S5V3AP-CGV2-VAL-1",
                                    46_100_000_000))
        self.assertFalse(valid_entry(decision, prereg, "S5V3AP-CGV2-VAL-1",
                                     45_999_999_999))
        decision["checks"]["all"] = False
        self.assertFalse(valid_entry(decision, prereg, "S5V3AP-CGV2-VAL-1",
                                     46_100_000_000))
        decision["checks"]["all"] = True
        decision["captured_at_utc"] = "2026-10-11T00:00:00+00:00"
        self.assertFalse(valid_entry(decision, prereg, "S5V3AP-CGV2-VAL-1",
                                     46_100_000_000))


if __name__ == "__main__":
    unittest.main()
