import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
RUN = '/data/stage5-v3/ops-competition-2026/runs/SF-005-ATTEMPT-01'
with patch.dict(os.environ, {'SF005_RUN': RUN}):
    SPEC = importlib.util.spec_from_file_location('sf005_capture_binding',
                                                  SCRIPTS / 'capture_controller.py')
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)


class FaultCaptureBinding(unittest.TestCase):
    def setUp(self):
        self.at = time.time()
        self.plan = {'id': 'SF-005-ATTEMPT-01', 'raw_root': RUN,
                     'capture_schedule_sha256': 'a' * 64,
                     'authorization_scope': 'ONE_SF005_SERVICE30_ONLY'}
        self.ready = {'manifest_sha256': 'b' * 64}

    def test_matching_preregistered_fault_plan_is_accepted(self):
        MODULE.validate_fault_binding(self.plan, 'a' * 64,
                                      self.ready, 'b' * 64)

    def test_wrong_capture_schedule_or_manifest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'identity/schedule'):
            MODULE.validate_fault_binding(self.plan, 'c' * 64,
                                          self.ready, 'b' * 64)
        with self.assertRaisesRegex(RuntimeError, 'identity/schedule'):
            MODULE.validate_fault_binding(self.plan, 'a' * 64,
                                          self.ready, 'c' * 64)


if __name__ == '__main__':
    unittest.main()
