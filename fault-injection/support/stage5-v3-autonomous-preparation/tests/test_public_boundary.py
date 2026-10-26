"""The public leak scan covers delayed trace supplements as well as first reads."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_public_boundary.py"


class PublicBoundaryTests(unittest.TestCase):
    def test_private_field_in_late_span_blocks_public_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run"
            for phase in ("before", "during", "after"):
                first = run / "capture-pipeline/captures" / phase
                later = run / "capture-pipeline/trace-completion" / phase
                first.mkdir(parents=True)
                later.mkdir(parents=True)
                (first / "capture-manifest.json").write_text("{}\n")
                (later / "completion-manifest.json").write_text("{}\n")
            late = run / "capture-pipeline/trace-completion/before/trace-storage-pages"
            late.mkdir()
            (late / "page-0000.json").write_text('{"fault_target":"private"}\n')
            output = Path(temp) / "audit.json"
            result = subprocess.run([sys.executable, str(SCRIPT), "--run", str(run),
                                     "--output", str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            audit = json.loads(output.read_text())
            self.assertEqual(audit["status"], "PRIVATE_FIELD_FOUND")
            self.assertEqual(audit["violations"][0]["forbidden_field"], "fault_target")
            self.assertIn("trace-completion/before", audit["violations"][0]["path"])


if __name__ == "__main__":
    unittest.main()
