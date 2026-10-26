import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import timer_lifecycle as lifecycle


class FakeSystemd:
    def __init__(self):
        self.units = {}
        self.commands = []
        self.stop_fails = False

    def run(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[0] == 'systemd-run':
            unit = next(x.split('=', 1)[1] for x in argv if x.startswith('--unit='))
            self.units[unit + '.timer'] = 'active'
            return subprocess.CompletedProcess(argv, 0, '', '')
        if argv[:2] == ['systemctl', 'show']:
            unit = argv[2]
            active = self.units.get(unit, 'inactive')
            loaded = 'loaded' if unit in self.units else 'not-found'
            return subprocess.CompletedProcess(argv, 0,
                f'ActiveState={active}\nLoadState={loaded}\n', '')
        if argv[:2] == ['systemctl', 'stop']:
            unit = argv[2]
            if self.stop_fails:
                return subprocess.CompletedProcess(argv, 1, '', 'forced stop error')
            if unit in self.units:
                self.units[unit] = 'inactive'
                return subprocess.CompletedProcess(argv, 0, '', '')
            return subprocess.CompletedProcess(argv, 5, '', 'not found')
        raise AssertionError(argv)


class TimerLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.systemd = FakeSystemd()
        binding = lifecycle.identity('SF-013-ATTEMPT-03',
            'SF-013-ATTEMPT-03-POD', 'a' * 64,
            'ops-sf013-sf-013-attempt-03-pod')
        self.binding = binding
        self.planned = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
        self.owner = {**binding, 'role': 'MASTER_CONTROLLER_ONLY',
            'controller_pid': 1234, 'planned_utc': self.planned,
            'issued_utc': datetime.now(timezone.utc).isoformat()}
        patcher = patch.object(lifecycle.subprocess, 'run', side_effect=self.systemd.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def arm(self, **kwargs):
        return lifecycle.arm(self.root, self.binding, self.planned,
                             ['/usr/bin/true'], self.owner, **kwargs)

    def test_normal_arm_and_idempotent_cancel(self):
        self.assertEqual(self.arm()['status'], 'ATTEMPT_TIMER_ARMED')
        first = lifecycle.revoke(self.root, self.binding)
        digest = hashlib.sha256((self.root / 'REVOKED.json').read_bytes()).hexdigest()
        second = lifecycle.revoke(self.root, self.binding)
        self.assertEqual(first['status'], second['status'])
        self.assertEqual(digest, hashlib.sha256((self.root / 'REVOKED.json').read_bytes()).hexdigest())
        self.assertFalse(lifecycle.is_active(self.systemd.units and
            lifecycle.state(self.binding['unit'] + '.timer')))

    def test_duplicate_arm_revokes_existing_timer(self):
        self.arm()
        with self.assertRaisesRegex(RuntimeError, 'DUPLICATE_ARM_REVOKED'):
            self.arm()
        self.assertTrue((self.root / 'REVOKED.json').exists())
        self.assertEqual(self.systemd.units[self.binding['unit'] + '.timer'], 'inactive')

    def test_error_after_actual_install_revokes_timer(self):
        with self.assertRaisesRegex(RuntimeError, 'injected partial failure'):
            self.arm(post_install_check=lambda: (_ for _ in ()).throw(
                RuntimeError('injected partial failure')))
        self.assertTrue((self.root / 'arm-intent.json').exists())
        self.assertTrue((self.root / 'REVOKED.json').exists())
        self.assertEqual(self.systemd.units[self.binding['unit'] + '.timer'], 'inactive')

    def test_wrong_owner_or_attempt_rejected_without_touching_timer(self):
        with self.assertRaisesRegex(RuntimeError, 'TIMER_ARM_OWNER_MISMATCH'):
            lifecycle.arm(self.root, self.binding, self.planned, ['/usr/bin/true'],
                          {**self.owner, 'action_id': 'OTHER'})
        self.assertFalse(any(command[0] == 'systemd-run'
                             for command in self.systemd.commands))
        self.assertTrue((self.root / 'REVOKED.json').exists())
        with self.assertRaisesRegex(RuntimeError, 'ATTEMPT_TIMER_IDENTITY'):
            lifecycle.identity('SF-013-ATTEMPT-03', 'SF-014-ATTEMPT-01-POD',
                               'a' * 64, self.binding['unit'])

    def test_revocation_does_not_touch_other_attempt_unit(self):
        other = lifecycle.identity('SF-013-ATTEMPT-04', 'SF-013-ATTEMPT-04-POD',
                                   'b' * 64, 'ops-sf013-sf-013-attempt-04-pod')
        self.systemd.units[self.binding['unit'] + '.timer'] = 'active'
        self.systemd.units[other['unit'] + '.timer'] = 'active'
        lifecycle.revoke(self.root, self.binding)
        self.assertEqual(self.systemd.units[other['unit'] + '.timer'], 'active')
        self.assertFalse(any(other['unit'] in ' '.join(command)
                         for command in self.systemd.commands
                         if command[:2] == ['systemctl', 'stop']))

    def test_failed_systemd_stop_never_reports_cleanup_pass(self):
        self.systemd.units[self.binding['unit'] + '.timer'] = 'active'
        self.systemd.stop_fails = True
        with self.assertRaisesRegex(RuntimeError, 'REVOCATION_UNVERIFIED'):
            lifecycle.revoke(self.root, self.binding)
        self.assertTrue((self.root / 'REVOKED.json').exists())
        self.assertFalse((self.root / 'timer-revocation.json').exists())


if __name__ == '__main__':
    unittest.main()
