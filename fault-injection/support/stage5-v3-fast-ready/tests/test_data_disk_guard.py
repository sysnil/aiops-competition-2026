import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/data_disk_guard.py"
spec = importlib.util.spec_from_file_location("data_disk_guard", SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class DataDiskGuardTests(unittest.TestCase):
    def test_reject_root_output(self):
        with self.assertRaises((ValueError, RuntimeError)):
            guard.assert_ready(Path("/root/stage5-v3-fast-ready/runs/new"))

    def test_reject_relative_output(self):
        with self.assertRaises((ValueError, RuntimeError)):
            guard.assert_ready(Path("stage5-v3/runs/new"))

    def test_approved_disk_uuid_constant(self):
        self.assertEqual(guard.UUID, "a35051a2-3d09-4b83-bbc0-3bb055e8a4f3")

    def test_data_floor_does_not_change_root_policy(self):
        self.assertEqual(guard.DATA_MIN_FREE_BYTES, 64 * 1024 ** 3)
        self.assertTrue(str(guard.POLICY).endswith("global-safety-policy-draft.json"))


if __name__ == "__main__":
    unittest.main()
