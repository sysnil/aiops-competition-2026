import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/start_formal.py'
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('sf008_start_formal_v1', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StartupEnvironment(unittest.TestCase):
    def setUp(self):
        self.plan = {'id': 'SF-013-ATTEMPT-03',
                     'raw_root': '/data/stage5-v3/ops-competition-2026/runs/SF-013-ATTEMPT-03',
                     'capture_controller_unit': 'ops-sf013-formal-capture03-20261019',
                     'controller_unit': 'ops-sf013-formal-controller03-20261019'}

    def test_both_units_receive_run_and_kubernetes_identity(self):
        for role in ('capture', 'controller'):
            with self.subTest(role=role):
                argv = module.command(self.plan, Path('/manifest'), 'registered-hash', role)
                self.assertIn('--setenv=SF013_RUN=' + self.plan['raw_root'], argv)
                self.assertIn('--setenv=KUBECONFIG=/etc/kubernetes/admin.conf', argv)

    def test_wrong_run_or_role_fails_before_start(self):
        with self.assertRaises(RuntimeError):
            module.command({**self.plan, 'id': 'SF-003-ATTEMPT-02'},
                           Path('/manifest'), 'registered-hash', 'controller')
        with self.assertRaises(ValueError):
            module.command(self.plan, Path('/manifest'), 'registered-hash', 'marker')

    def test_capture_start_failure_still_cancels_exact_worker_attempt(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv == ['capture']:
                raise RuntimeError('capture systemd-run failed')
            return None
        with patch.object(module.subprocess, 'run', side_effect=fake_run), \
             patch.object(module, 'stop_bound_timer') as cancel:
            with self.assertRaisesRegex(RuntimeError, 'capture systemd-run failed'):
                module.launch_units(self.plan, Path('/manifest'), 'a' * 64,
                                    Path('/run'), ['capture'], ['guard'], ['controller'])
        cancel.assert_called_once()
        self.assertEqual(calls, [['capture'],
            ['systemctl', 'stop', self.plan['controller_unit'] + '.service'],
            ['systemctl', 'stop', self.plan['capture_controller_unit'] + '.service']])

    def test_guard_or_controller_start_failure_cancels(self):
        for failed in ('guard', 'controller'):
            with self.subTest(failed=failed):
                def fake_run(argv, **kwargs):
                    if argv == [failed]:
                        raise RuntimeError(failed + ' startup failed')
                    return None
                with patch.object(module.subprocess, 'run', side_effect=fake_run), \
                     patch.object(module, 'wait_capture_ready', return_value='health'), \
                     patch.object(module, 'stop_bound_timer') as cancel:
                    with self.assertRaisesRegex(RuntimeError, failed + ' startup failed'):
                        module.launch_units(self.plan, Path('/manifest'), 'a' * 64,
                                            Path('/run'), ['capture'], ['guard'], ['controller'])
                cancel.assert_called_once()

    def test_sigterm_and_unverified_cleanup_fail_closed(self):
        with patch.object(module.subprocess, 'run', side_effect=[
                SystemExit(143), None, None]), \
             patch.object(module, 'stop_bound_timer') as cancel:
            with self.assertRaises(SystemExit):
                module.launch_units(self.plan, Path('/manifest'), 'a' * 64,
                                    Path('/run'), ['capture'], ['guard'], ['controller'])
        cancel.assert_called_once()
        with patch.object(module.subprocess, 'run', side_effect=[
                RuntimeError('capture failed'), None, None]), \
             patch.object(module, 'stop_bound_timer', side_effect=RuntimeError('SSH down')):
            with self.assertRaisesRegex(RuntimeError, 'STARTUP_CLEANUP_UNVERIFIED'):
                module.launch_units(self.plan, Path('/manifest'), 'a' * 64,
                                    Path('/run'), ['capture'], ['guard'], ['controller'])


if __name__ == '__main__':
    unittest.main()
