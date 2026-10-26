import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import patch
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
RUN = '/data/stage5-v3/ops-competition-2026/runs/SF-013-ATTEMPT-03'
with patch.dict(os.environ, {'SF013_RUN': RUN}):
    SPEC = importlib.util.spec_from_file_location('sf008_pod300_capture',
                                                  SCRIPTS / 'capture_controller.py')
    MODULE = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(MODULE)


class Pod300TimingContract(unittest.TestCase):
    def test_capture_waits_for_registered_recovery_and_postcheck(self):
        plan = {'actions': [{'offset_seconds': 600, 'duration_seconds': 300,
                             'recovery_offset_seconds': 1140}]}
        deadline = MODULE.fault_terminal_deadline_offset(plan)
        self.assertEqual(deadline, 1380)
        self.assertGreater(deadline, 1140 + 60 + 120)
        self.assertLess(deadline, 1800)

    def test_old_sixty_second_recovery_schedule_is_rejected(self):
        plan = {'actions': [{'offset_seconds': 600, 'duration_seconds': 300,
                             'recovery_offset_seconds': 840}]}
        with self.assertRaisesRegex(RuntimeError, 'recovery timeline'):
            MODULE.fault_terminal_deadline_offset(plan)


if __name__ == '__main__':
    unittest.main()
