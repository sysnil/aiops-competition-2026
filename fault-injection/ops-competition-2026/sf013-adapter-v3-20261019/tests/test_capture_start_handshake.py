"""The Worker timer must never be armed behind a failed capture start."""

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


FILE = Path(__file__).resolve().parents[1] / 'scripts/start_formal.py'
sys.path.insert(0, str(FILE.parent))
SPEC = importlib.util.spec_from_file_location('sf013_capture_handshake', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CaptureHandshakeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.identity = self.run / 'identity.json'
        self.initial = self.run / 'control/capture-initial-health.json'
        self.initial.parent.mkdir()
        self.plan = {'identity_file': str(self.identity),
                     'capture_controller_unit': 'capture-test',
                     'collector_uid': 'collector-uid'}

    def test_identity_freshness_is_checked_before_capture(self):
        now = datetime.now(timezone.utc).timestamp()
        self.identity.write_text(json.dumps({
            'status': 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED',
            'at_utc': datetime.fromtimestamp(now - 100, timezone.utc).isoformat()}))
        self.plan['identity_sha256'] = MODULE.sha(self.identity)
        self.assertAlmostEqual(MODULE.identity_age(self.plan, now), 100, delta=1)
        with self.assertRaisesRegex(RuntimeError, 'refreshed within 180 seconds'):
            MODULE.identity_age(self.plan, now + 81)
        self.identity.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'hash changed'):
            MODULE.identity_age(self.plan, now)

    def test_controller_requires_live_capture_and_matching_evidence(self):
        self.initial.write_text(json.dumps({
            'environment': {'status': 'LIVE_ENVIRONMENT_PASS'},
            'collector': {'uid': 'collector-uid',
                          'refused': {'grpc': 0.0, 'http': 0.0}}}))
        active = subprocess.CompletedProcess([], 0, 'active\n', '')
        with patch.object(MODULE.subprocess, 'run', return_value=active):
            self.assertEqual(MODULE.wait_capture_ready(self.plan, self.run, .2),
                             MODULE.sha(self.initial))
        inactive = subprocess.CompletedProcess([], 3, 'inactive\n', '')
        with patch.object(MODULE.subprocess, 'run', return_value=inactive):
            with self.assertRaisesRegex(RuntimeError, 'exited before readiness'):
                MODULE.wait_capture_ready(self.plan, self.run, .2)
        self.initial.write_text(json.dumps({
            'environment': {'status': 'LIVE_ENVIRONMENT_PASS'},
            'collector': {'uid': 'wrong', 'refused': {'grpc': 0.0, 'http': 0.0}}}))
        with patch.object(MODULE.subprocess, 'run', return_value=active):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                MODULE.wait_capture_ready(self.plan, self.run, .2)


if __name__ == '__main__':
    unittest.main()
