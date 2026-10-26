import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_full_clean


def responses(*, pressure=False, infra=4, free=46_000_000_000):
    nodes = {"items": [{"status": {"conditions": [
        {"type": "Ready", "status": "True"},
        {"type": "DiskPressure", "status": "True" if pressure else "False"},
        {"type": "MemoryPressure", "status": "False"},
        {"type": "PIDPressure", "status": "False"}]}} for _ in range(4)]}
    return [json.dumps(nodes), json.dumps({"status": {"numberReady": infra}}),
            json.dumps({"status": {"readyReplicas": 1}}), str(free)]


class LiveSafetyTickTests(unittest.TestCase):
    def test_all_healthy(self):
        with patch.object(run_full_clean, "remote", side_effect=responses()):
            run_full_clean.safety_tick()

    def test_pressure_aborts(self):
        with patch.object(run_full_clean, "remote", side_effect=responses(pressure=True)):
            with self.assertRaisesRegex(RuntimeError, "SAFETY_ABORT"):
                run_full_clean.safety_tick()

    def test_infra_missing_aborts(self):
        with patch.object(run_full_clean, "remote", side_effect=responses(infra=3)):
            with self.assertRaisesRegex(RuntimeError, "SAFETY_ABORT"):
                run_full_clean.safety_tick()

    def test_low_storage_aborts(self):
        with patch.object(run_full_clean, "remote", side_effect=responses(free=20_000_000_000)):
            with self.assertRaisesRegex(RuntimeError, "SAFETY_ABORT"):
                run_full_clean.safety_tick()


if __name__ == "__main__":
    unittest.main()
