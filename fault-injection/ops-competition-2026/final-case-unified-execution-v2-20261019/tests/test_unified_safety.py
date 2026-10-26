import json
import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from action_decision import permit as write_permit, revoke
from entry_policy import validate_action
from http_gate import cancel_other_actions, controlled_request_factory, header_for_request
from master_authorizer import authorize, protocol
from worker_entry import execute


class UnifiedSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = time.time()
        self.worker = 'k8s-worker2'
        self.case = 'SF-013'
        self.api = protocol(self.worker)
        self.key = Ed25519PrivateKey.generate()
        self.run = self.case + '-ATTEMPT-MARKERTEST'
        self.action_id = self.run + '-POD'
        self.manifest = 'a' * 64
        self.manifest_file = self.root / 'manifest.json'
        self.manifest_file.write_text('{"test":"harmless"}\n')
        self.manifest = hashlib.sha256(self.manifest_file.read_bytes()).hexdigest()
        self.boot = '11111111-1111-1111-1111-111111111111'
        self.req = self.api.request(self.run, self.action_id, self.manifest,
                                    'MARKER_ONLY', self.worker,
                                    '22222222-2222-2222-2222-222222222222', self.now)
        self.directory = self.root / self.run / self.action_id
        self.directory.mkdir(parents=True)
        self.state = {'status': 'REGISTERED', 'run_id': self.run,
                      'action_id': self.action_id, 'manifest_sha256': self.manifest,
                      'capability': 'MARKER_ONLY', 'worker_host': self.worker,
                      'worker_boot_id': self.req['worker_boot_id'],
                      'master_boot_id': self.boot, 'planned_utc': self.api.now_utc(self.now),
                      'manifest_path': str(self.manifest_file),
                      'controller_unit': 'ops-sf013-control', 'controller_pid': 1234,
                      'guard_unit': 'ops-sf013-guard'}
        self.permit = {'status': 'PERMITTED', 'run_id': self.run,
                       'action_id': self.action_id, 'manifest_sha256': self.manifest,
                       'state_sha256': self.api.digest(self.state),
                       'issued_utc': self.api.now_utc(self.now - 1),
                       'expires_utc': self.api.now_utc(self.now + 2)}
        self.save('state.json', self.state)
        self.save('permit.json', self.permit)

    def save(self, name, value):
        (self.directory / name).write_text(json.dumps(value))

    def unit(self, name):
        return {'ActiveState': 'active', 'MainPID':
                '1234' if name.endswith('control') else '5678', 'Result': 'success'}

    def grant(self, units=None, req=None, clock=None):
        return authorize(req or self.req, state_root=self.root,
                         units=units or self.unit, current_boot=lambda: self.boot,
                         private_key=self.key, clock=clock or (lambda: self.now))

    def identity(self):
        return {k: self.state[k] for k in
                ('run_id', 'action_id', 'manifest_sha256')}

    def test_normal_one_shot_and_replay(self):
        response = self.grant()
        self.api.verify_grant(self.key.public_key(), response, self.req,
                              self.boot, self.now + .1, self.directory / 'worker-used.json')
        with self.assertRaises(FileExistsError):
            self.api.verify_grant(self.key.public_key(), response, self.req,
                                  self.boot, self.now + .1,
                                  self.directory / 'worker-used.json')
        with self.assertRaisesRegex(ValueError, 'ALREADY_CONSUMED'):
            self.grant()

    def test_revoked_before_sign(self):
        revoke(self.directory, self.identity(), 'test')
        with self.assertRaisesRegex(ValueError, 'REVOKED'):
            self.grant()

    def test_revoke_during_controller_check_blocks_grant(self):
        began = threading.Event()
        release = threading.Event()
        outcome = []

        def slow_unit(name):
            began.set()
            release.wait(2)
            return self.unit(name)

        def sign():
            try:
                self.grant(units=slow_unit)
                outcome.append('GRANTED')
            except ValueError as error:
                outcome.append(str(error))

        thread = threading.Thread(target=sign)
        thread.start()
        self.assertTrue(began.wait(2))
        revoker = threading.Thread(target=lambda: revoke(self.directory,
                                                          self.identity(), 'test'))
        revoker.start()
        limit = time.monotonic() + 2
        while not (self.directory / 'REVOCATION_REQUESTED.json').exists():
            self.assertLess(time.monotonic(), limit)
            time.sleep(.001)
        release.set()
        thread.join(3)
        revoker.join(3)
        self.assertEqual(len(outcome), 1)
        self.assertIn('REVOKE', outcome[0])
        self.assertFalse((self.directory / 'consumed.json').exists())
        self.assertTrue((self.directory / 'REVOKED.json').exists())

    def test_post_sign_revoke_leaves_bounded_grant(self):
        response = self.grant()
        revoke(self.directory, self.identity(), 'test')
        self.api.verify_grant(self.key.public_key(), response, self.req,
                              self.boot, self.now + .09)
        with self.assertRaises(ValueError):
            self.api.verify_grant(self.key.public_key(), response, self.req,
                                  self.boot, self.now + 1.21)

    def test_master_failure_controller_guard_and_expiry(self):
        with self.assertRaisesRegex(ValueError, 'NOT_LIVE'):
            self.grant(units=lambda name: {'ActiveState': 'inactive',
                                           'MainPID': '0', 'Result': 'exit-code'})
        with self.assertRaisesRegex(ValueError, 'REQUEST_SCOPE_OR_TIME'):
            self.grant(clock=lambda: self.now + 3)

    def test_manifest_changes_during_check_denied(self):
        def unit(name):
            self.manifest_file.write_text('{"test":"changed"}\n')
            return self.unit(name)
        with self.assertRaisesRegex(ValueError, 'STATE_CHANGED_OR_EXPIRED'):
            self.grant(units=unit)

    def test_manifest_and_action_isolation(self):
        wrong = {**self.req, 'manifest_sha256': 'b' * 64}
        with self.assertRaises(ValueError):
            self.grant(req=wrong)
        with self.assertRaisesRegex(ValueError, 'REVOKE_IDENTITY'):
            revoke(self.directory, {**self.identity(), 'action_id': 'other'}, 'test')
        self.assertFalse((self.directory / 'REVOCATION_REQUESTED.json').exists())

    def test_two_attempts_are_separate(self):
        other = self.root / 'SF-016-ATTEMPT-MARKERTEST' / 'SF-016-ATTEMPT-MARKERTEST-POD'
        other.mkdir(parents=True)
        other_state = {**self.state, 'run_id': other.parent.name, 'action_id': other.name}
        (other / 'state.json').write_text(json.dumps(other_state))
        revoke(self.directory, self.identity(), 'test')
        self.assertFalse((other / 'REVOKED.json').exists())

    def test_permit_cannot_follow_revocation(self):
        revoke(self.directory, self.identity(), 'test')
        with self.assertRaisesRegex(ValueError, 'TERMINAL'):
            write_permit(self.directory, self.identity(), self.permit)

    def action(self, case='SF-015', kind='HTTP', worker='k8s-master',
               mechanism='HTTPReqDelay', duration=300):
        run = case + '-ATTEMPT-MARKERTEST'
        return {'run_id': run, 'action_id': run + '-' + kind,
                'kind': kind, 'worker': worker, 'mechanism': mechanism,
                'duration_seconds': duration, 'manifest_sha256': 'a' * 64,
                'capability': 'BOUND_QUALIFIED_FAULT',
                'entrypoint': '/root/ops-competition-2026/final-case-unified-execution-v2-20261019/scripts/http_gate.py'}

    def http_state(self, action):
        root = self.root / 'http'
        root.mkdir()
        state = {'status': 'REGISTERED', **{k: action[k] for k in
                 ('run_id', 'action_id', 'manifest_sha256')},
                 'planned_start_epoch': self.now - 1,
                 'planned_end_epoch': self.now + 300,
                 'requester_pid': os.getpid(), 'requester_start_ticks': 55,
                 'controller_pid': os.getpid(), 'controller_start_ticks': 55,
                 'controller_unit': 'ops-sf015-control',
                 'guard_unit': 'ops-sf015-guard'}
        (root / 'state.json').write_text(json.dumps(state))
        (root / 'permit.json').write_text(json.dumps({
            'status': 'PERMITTED', **{k: action[k] for k in
            ('run_id', 'action_id', 'manifest_sha256')},
            'issued_epoch': self.now - 1, 'expires_epoch': self.now + 300}))
        return root

    def test_http_master_gate_and_controls(self):
        action = self.action()
        root = self.http_state(action)
        kwargs = {'unit_state': lambda name: {'ActiveState': 'active',
                    'MainPID': str(os.getpid())}, 'clock': lambda: self.now,
                  'pid_start': lambda pid: 55}
        self.assertEqual(header_for_request(action, root, 'GET', 'http://x/', **kwargs),
                         {'x-envoy-fault-delay-request': '250'})
        self.assertEqual(header_for_request(action, root, 'GET', 'http://x/cart', **kwargs), {})
        self.assertEqual(header_for_request(action, root, 'GET', 'http://x/image', **kwargs), {})
        observed = []
        def original(url, **kwargs):
            observed.append((url, kwargs['headers']))
            return kwargs
        factory = controlled_request_factory(action, root, original, **kwargs)
        factory('http://x/', method='GET')
        factory('http://x/cart', method='GET')
        self.assertEqual(observed[0][1]['x-envoy-fault-delay-request'], '250')
        self.assertNotIn('x-envoy-fault-delay-request', observed[1][1])
        revoke(root, {k: action[k] for k in ('run_id', 'action_id', 'manifest_sha256')}, 'test')
        with self.assertRaisesRegex(ValueError, 'REVOKED'):
            header_for_request(action, root, 'GET', 'http://x/', **kwargs)

    def test_http_controller_exit_or_late_response(self):
        action = self.action()
        root = self.http_state(action)
        with self.assertRaisesRegex(ValueError, 'NOT_LIVE'):
            header_for_request(action, root, 'GET', 'http://x/',
                               unit_state=lambda name: {'ActiveState':'inactive','MainPID':'0'}, clock=lambda: self.now,
                               pid_start=lambda pid: 55)
        with self.assertRaisesRegex(ValueError, 'NOT_ACTIVE'):
            header_for_request(action, root, 'GET', 'http://x/',
                               unit_state=lambda name: {'ActiveState':'active','MainPID':str(os.getpid())},
                               clock=lambda: self.now + 301, pid_start=lambda pid: 55)

    def test_worker_marker_shared_gate_and_local_revoke(self):
        action = self.action('SF-016', 'POD', 'k8s-worker2', 'PodMemoryStress', 300)
        action.update(capability='MARKER_ONLY', effect='MARKER')
        action['entrypoint'] = ('/root/ops-competition-2026/'
            'final-case-unified-execution-v2-20261019/scripts/marker_cli.py')
        root = self.root / 'worker'
        root.mkdir()
        (root / 'auth-binding.json').write_text(json.dumps({
            **{k: action[k] for k in ('run_id', 'action_id', 'manifest_sha256', 'capability')},
            'worker_host': 'k8s-worker2'}))
        auth = SimpleNamespace(execute_once=lambda binding, runtime, callback:
                               callback({'nonce': 'marker'}))
        self.assertEqual(execute(action, root, lambda grant: 'MARKER',
                                 auth_module=auth, marker=True), 'MARKER')
        (root / 'REVOKED.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'LOCAL_REVOKED'):
            execute(action, root, lambda grant: 'MARKER', auth_module=auth, marker=True)

    def test_legacy_path_rejected_and_dual_action_cancel(self):
        http = self.action('SF-017', duration=240)
        service = self.action('SF-017', 'SERVICE', 'k8s-worker3', 'ServicePause', 240)
        service['entrypoint'] = '/root/sf007-real-adapter-v7-20261018/fault_worker.py'
        with self.assertRaisesRegex(ValueError, 'LEGACY'):
            validate_action(service)
        service['entrypoint'] = ('/root/ops-competition-2026/'
            'final-case-unified-execution-v2-20261019/scripts/worker_entry.py')
        cancelled = []
        cancel_other_actions([http, service], http,
                             lambda action: cancelled.append(action['action_id']))
        self.assertEqual(cancelled, [service['action_id']])
        other = self.action('SF-015')
        with self.assertRaisesRegex(ValueError, 'CROSS_ATTEMPT'):
            cancel_other_actions([other], http, lambda action: None)


if __name__ == '__main__':
    unittest.main()
