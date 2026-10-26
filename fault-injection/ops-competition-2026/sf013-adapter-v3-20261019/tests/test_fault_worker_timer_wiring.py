import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import fault_worker as worker


class FaultWorkerTimerWiring(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.manifest = SimpleNamespace(run_id='SF-013-ATTEMPT-03')
        self.action = {'action_id': 'SF-013-ATTEMPT-03-POD',
            'planned_utc': (datetime.now(timezone.utc) +
                            timedelta(minutes=2)).isoformat(),
            'unit': 'ops-sf013-sf-013-attempt-03-pod',
            'runtime': str(self.runtime)}
        self.plan = {'controller_unit': 'ops-sf013-formal-controller03-20261019'}
        self.args = SimpleNamespace(manifest=Path('/tmp/synthetic-only-manifest'),
            manifest_sha='a' * 64, action_id=self.action['action_id'])

    def bound(self, *_args):
        return ({'run_id': self.manifest.run_id}, self.plan,
                self.action, self.runtime)

    def test_arm_requires_fresh_controller_owner(self):
        with patch.object(worker, 'bind', side_effect=self.bound), \
             patch.object(worker, 'revoke') as revoke:
            with self.assertRaisesRegex(RuntimeError, 'ARM_OWNER_MISSING'):
                worker.arm(self.args)
        self.assertEqual(revoke.call_args.args[1]['action_id'], self.action['action_id'])

    def test_arm_passes_exact_attempt_binding_to_lifecycle_module(self):
        owner = {'run_id': self.manifest.run_id,
            'action_id': self.action['action_id'],
            'manifest_sha256': self.args.manifest_sha,
            'unit': self.action['unit'],
            'controller_unit': self.plan['controller_unit'],
            'worker': 'k8s-worker2', 'role': 'MASTER_CONTROLLER_ONLY',
            'controller_pid': 1234, 'issued_utc': datetime.now(timezone.utc).isoformat(),
            'planned_utc': self.action['planned_utc']}
        (self.runtime / 'arm-owner.json').write_text(json.dumps(owner))
        with patch.object(worker, 'bind', side_effect=self.bound), \
             patch.object(worker.socket, 'gethostname', return_value='k8s-worker2'), \
             patch.object(worker, 'arm_timer', return_value={'status': 'ATTEMPT_TIMER_ARMED'}) as arm:
            result = worker.arm(self.args)
        self.assertEqual(result['status'], 'ATTEMPT_TIMER_ARMED')
        self.assertEqual(arm.call_args.args[1]['run_id'], self.manifest.run_id)
        self.assertEqual(arm.call_args.args[1]['unit'], self.action['unit'])

    def test_cancel_is_idempotent_and_persists_exact_attempt_receipt(self):
        states = {'.timer': {'ActiveState': 'inactive', 'LoadState': 'not-found'},
                  '.service': {'ActiveState': 'inactive', 'LoadState': 'not-found'}}
        with patch.object(worker, 'bind', side_effect=self.bound), \
             patch.object(worker, 'revoke', return_value={
                 'status': 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE', 'states': states}) as revoke:
            first = worker.cancel(self.args)
            before = hashlib.sha256((self.runtime / 'cancelled.json').read_bytes()).hexdigest()
            second = worker.cancel(self.args)
        self.assertEqual(first['status'], second['status'])
        self.assertEqual(revoke.call_count, 2)
        self.assertEqual(before, hashlib.sha256(
            (self.runtime / 'cancelled.json').read_bytes()).hexdigest())
        self.assertEqual(json.loads((self.runtime / 'cancelled.json').read_text())['run_id'],
                         self.manifest.run_id)

    def test_revoked_short_lease_cannot_fire(self):
        manifest = {'run_id': self.manifest.run_id}
        lease = {'run_id': self.manifest.run_id,
            'action_id': self.action['action_id'], 'manifest_sha256': 'a' * 64,
            'permission': 'BOUND_QUALIFIED_FAULT'}
        with self.assertRaisesRegex(RuntimeError, 'REVOKED'):
            worker.validate_lease(manifest, 'a' * 64, self.action, lease,
                                  datetime.now(timezone.utc).timestamp(), revoked=True)


if __name__ == '__main__':
    unittest.main()
