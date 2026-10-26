import os
import subprocess
import sys
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
RUN = '/data/stage5-v3/ops-competition-2026/runs/SF-010-ATTEMPT-03'


class CapturePreflight(unittest.TestCase):
    def test_main_does_not_shadow_module_level_sys(self):
        environment = dict(os.environ, SF010_RUN=RUN)
        result = subprocess.run([sys.executable, '-B', '-c',
            'from capture_controller import main; print("sys" in main.__code__.co_varnames)'],
            env=dict(environment, PYTHONPATH=str(SCRIPTS)), capture_output=True,
            text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'False')

    def test_missing_kubeconfig_fails_before_creating_run(self):
        environment = dict(os.environ, SF010_RUN=RUN)
        environment.pop('KUBECONFIG', None)
        result = subprocess.run([sys.executable, '-B', '-c',
            'from capture_controller import prereq; prereq()'],
            env=dict(environment, PYTHONPATH=str(SCRIPTS)), capture_output=True,
            text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Explicit Kubernetes credential context required', result.stderr)


if __name__ == '__main__':
    unittest.main()
