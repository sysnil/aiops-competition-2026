import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
RUN = '/data/stage5-v3/ops-competition-2026/runs/SF-011-ATTEMPT-03'
os.environ.setdefault('SF011_RUN', RUN)
sys.path.insert(0, str(SCRIPTS))
from capture_controller import read, reject_other_competition_units


class CapturePreflight(unittest.TestCase):
    def test_registered_heartbeat_string_path_is_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'five-host-storage.json'
            path.write_text('{"status": "DYNAMIC_STORAGE_PASS"}')
            plan = {'five_host_heartbeat_path': str(path)}
            self.assertEqual(read(plan['five_host_heartbeat_path'])['status'],
                             'DYNAMIC_STORAGE_PASS')

    def test_current_observer_controller_and_capture_are_allowed(self):
        units = '\n'.join(
            name + ' loaded active running' for name in (
                'ops-sf011-formal-observer03-20261017.service',
                'ops-sf011-formal-controller03-20261017.service',
                'ops-sf011-formal-capture03-20261017.service'))
        reject_other_competition_units(units)

    def test_old_or_unregistered_competition_unit_is_rejected(self):
        for name in ('ops-sf011-formal-observer03-20261015.service',
                     'ops-sf011-unknown.service', 'ops-sf002-other.service',
                     'ops-sf008-formal-controller03-20261017.service'):
            with self.subTest(name=name), self.assertRaisesRegex(
                    RuntimeError, 'Another competition run'):
                reject_other_competition_units(name + ' loaded active running')

    def test_main_does_not_shadow_module_level_sys(self):
        environment = dict(os.environ, SF011_RUN=RUN)
        result = subprocess.run([sys.executable, '-B', '-c',
            'from capture_controller import main; print("sys" in main.__code__.co_varnames)'],
            env=dict(environment, PYTHONPATH=str(SCRIPTS)), capture_output=True,
            text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'False')

    def test_missing_kubeconfig_fails_before_creating_run(self):
        environment = dict(os.environ, SF011_RUN=RUN)
        environment.pop('KUBECONFIG', None)
        result = subprocess.run([sys.executable, '-B', '-c',
            'from capture_controller import prereq; prereq()'],
            env=dict(environment, PYTHONPATH=str(SCRIPTS)), capture_output=True,
            text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Explicit Kubernetes credential context required', result.stderr)


if __name__ == '__main__':
    unittest.main()
