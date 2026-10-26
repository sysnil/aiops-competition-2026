import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import sf003_controller as controller
import start_formal as starter
import timer_guard


class ControllerTimerCleanup(unittest.TestCase):
    def test_preexisting_timer_is_cancelled_and_controller_refuses_to_arm(self):
        backend = Mock(side_effect=[json.dumps({'units': {
            '.timer': {'ActiveState': 'active'},
            '.service': {'ActiveState': 'inactive'}},
            'armed_record': True, 'revoked_record': False,
            'started_record': False}), json.dumps({'status': 'ATTEMPT_CLEANUP_VERIFIED'})])
        with self.assertRaisesRegex(RuntimeError, 'PREEXISTING_ATTEMPT_TIMER_REVOKED'):
            controller.ensure_unarmed(backend)
        self.assertEqual([item.args[0] for item in backend.call_args_list],
                         ['inspect', 'cancel'])

    def test_cleanup_is_unconditional_and_repeatable(self):
        backend = Mock(return_value=json.dumps({'status': 'ATTEMPT_CLEANUP_VERIFIED'}))
        for _ in range(2):
            result = {'status': 'ABORT', 'armed': False}
            self.assertTrue(controller.cleanup_attempt(backend, result))
            self.assertTrue(result['worker_cleanup_verified'])
        self.assertEqual(backend.call_count, 2)

    def test_unverified_cleanup_cannot_be_reported_as_safe(self):
        result = {'status': 'ABORT'}
        self.assertFalse(controller.cleanup_attempt(Mock(side_effect=RuntimeError('SSH down')), result))
        self.assertEqual(result['status'], 'SF013_CLEANUP_UNVERIFIED')

    def test_starter_checks_timer_and_service_and_guards_its_own_unit(self):
        plan = {'id': 'SF-013-ATTEMPT-03',
                'raw_root': '/data/stage5-v3/ops-competition-2026/runs/SF-013-ATTEMPT-03',
                'capture_controller_unit': 'capture',
                'controller_unit': 'controller',
                'timer_guard_unit': 'controller-guard',
                'actions': [{'unit': 'ops-sf013-sf-013-attempt-03-pod'}]}
        with patch.object(starter, 'remote', side_effect=['inactive\n', 'active\n']):
            self.assertTrue(starter.timer_pending(plan))
        argv = starter.command(plan, Path('/manifest'), 'a' * 64, 'guard')
        self.assertIn('--property=Restart=on-failure', argv)
        self.assertIn('--unit=controller-guard', argv)

    def test_independent_guard_cancels_when_controller_disappears(self):
        with unittest.mock.patch.object(timer_guard, 'unit_active', return_value=False), \
             unittest.mock.patch.object(timer_guard.time, 'monotonic', return_value=0):
            result = timer_guard.watch_controller('controller', '/missing/terminal',
                '/missing/started', time.time() + 10,
                lambda: {'status': 'ATTEMPT_CLEANUP_VERIFIED'},
                startup_grace=0)
        self.assertEqual(result['status'], 'GUARD_ABORTED_ORPHANED_CONTROLLER')


if __name__ == '__main__':
    unittest.main()
