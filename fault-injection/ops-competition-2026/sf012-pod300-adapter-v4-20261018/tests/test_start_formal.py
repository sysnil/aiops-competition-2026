import importlib.util
from pathlib import Path
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/start_formal.py'
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('sf008_start_formal_v1', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StartupEnvironment(unittest.TestCase):
    def setUp(self):
        self.plan = {'id': 'SF-012-ATTEMPT-04',
                     'raw_root': '/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-04',
                     'capture_controller_unit': 'ops-sf012-formal-capture04-20261018',
                     'controller_unit': 'ops-sf012-formal-controller04-20261018'}

    def test_both_units_receive_run_and_kubernetes_identity(self):
        for role in ('capture', 'controller'):
            with self.subTest(role=role):
                argv = module.command(self.plan, Path('/manifest'), 'registered-hash', role)
                self.assertIn('--setenv=SF012_RUN=' + self.plan['raw_root'], argv)
                self.assertIn('--setenv=KUBECONFIG=/etc/kubernetes/admin.conf', argv)

    def test_wrong_run_or_role_fails_before_start(self):
        with self.assertRaises(RuntimeError):
            module.command({**self.plan, 'id': 'SF-003-ATTEMPT-02'},
                           Path('/manifest'), 'registered-hash', 'controller')
        with self.assertRaises(ValueError):
            module.command(self.plan, Path('/manifest'), 'registered-hash', 'marker')


if __name__ == '__main__':
    unittest.main()
