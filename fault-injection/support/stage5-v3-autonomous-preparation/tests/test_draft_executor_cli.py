"""CLI stays synthetic-only and keeps the GT barrier closed."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BASE = Path(__file__).resolve().parents[1]
CLI = BASE / "scripts/draft_executor_cli.py"
ALLOCATION = BASE / "design/dataset-allocation-draft.yaml"
METHODS = BASE / "design/synthetic-method-scope.json"


class CliTests(unittest.TestCase):
    def test_requires_synthetic_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "archive"
            command = [sys.executable, str(CLI), "--root", str(root), "status"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("synthetic-only marker", result.stderr)

    def test_draft_registration_and_early_gt_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "archive"
            base = [sys.executable, str(CLI), "--root", str(root)]
            subprocess.run([*base, "init"], check=True, capture_output=True)
            subprocess.run([*base, "register", "--allocation", str(ALLOCATION),
                            "--method-scope", str(METHODS)], check=True,
                           capture_output=True)
            result = subprocess.run([*base, "unseal", "--split", "DEV",
                "--synthetic-gt", str(root / "private_synthetic_gt/DEV.json")],
                capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("GT barrier", result.stderr)
            self.assertEqual(len(list((root / "events").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
