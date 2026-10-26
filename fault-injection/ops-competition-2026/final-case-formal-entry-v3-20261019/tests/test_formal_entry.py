import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SHARED = Path('/root/ops-competition-2026/final-case-unified-execution-v1-20261019/scripts')
if not SHARED.is_dir():
    SHARED = Path(__file__).resolve().parents[2] / 'final-case-unified-execution-v1-20261019' / 'scripts'
sys.path.insert(0, str(SHARED))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from action_decision import revoke
from control_contract import ROOT, SHARED as PRODUCTION_SHARED, load_manifest, sha
from entry_policy import CASE_ACTIONS
from formal_controller import Sequence, SystemIO, HTTP_RUNTIME_MAX_SECONDS
from http_workload import request_factory
from storage_policy import check as storage_check, ROOTS
from timer_lifecycle import cancel as cancel_timer
import worker_fault_entry


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class FakeIO:
    def __init__(self, status='ACTION_TERMINAL', guard=True, fail_at=None):
        self.status = status
        self.guard = guard
        self.fail_at = fail_at
        self.events = []

    def register(self, action):
        self.events.append(('register', action['action_id']))

    def arm(self, action):
        self.events.append(('arm', action['action_id']))

    def preflight(self, action):
        self.events.append(('preflight', action['action_id']))

    def permit(self, action):
        self.events.append(('permit', action['action_id']))

    def await_terminal(self, action, **kwargs):
        self.events.append(('await', action['action_id']))
        return {'status': 'FAILED_CLOSED' if action['kind'] == self.fail_at else self.status}

    def revoke(self, action, reason):
        self.events.append(('revoke', action['action_id']))
        return {'status': 'REVOKED', 'reason': reason}

    def guard_running(self):
        return self.guard


class FormalEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.now = 1792398000.0

    def source_hashes(self):
        names = ['control_contract.py', 'worker_fault_entry.py',
                 'timer_lifecycle.py', 'qualified_effect.py',
                 'http_workload.py', 'formal_controller.py', 'formal_guard.py',
                 'start_formal.py', 'safety_monitor.py']
        shared = ['action_decision.py', 'auth_protocol.py', 'entry_policy.py',
                  'http_gate.py', 'master_authorizer.py', 'worker_auth.py',
                  'worker_entry.py']
        return {**{str(ROOT / 'scripts' / name): sha(ROOT / 'scripts' / name)
                   for name in names},
                **{str(PRODUCTION_SHARED / name): sha(PRODUCTION_SHARED / name)
                   for name in shared}}

    def fixture(self, case, *, mode='HARMLESS_REHEARSAL'):
        run = case + '-ATTEMPT-MARKERTEST-ABCDEFGH'
        actions = []
        for index, (kind, (worker, mechanism, duration)) in enumerate(
                CASE_ACTIONS[case].items()):
            action = {'run_id': run, 'action_id': run + '-' + kind,
                      'kind': kind, 'worker': worker, 'mechanism': mechanism,
                      'duration_seconds': duration,
                      'capability': 'MARKER_ONLY', 'effect': 'MARKER',
                      'entrypoint': str(SHARED / 'marker_cli.py'),
                      'timer_command': str(ROOT / 'scripts' /
                          ('http_workload.py' if kind == 'HTTP' else
                           'worker_fault_entry.py')),
                      'planned_start_utc': iso(self.now + 20 + index * 100),
                      'runtime': str(ROOT / 'runtime' / run / (run + '-' + kind))}
            if worker != 'k8s-master':
                action['unit'] = 'ops-' + case.lower().replace('-', '') + '-' + action['action_id'].lower()
            actions.append(action)
        manifest = {'schema': 'FINAL_CASE_UNIFIED_ENTRY_V1',
                    'run_id': run, 'mode': mode, 'actions': actions,
                    'source_sha256': self.source_hashes(),
                    'controller_unit': 'ops-' + case.lower().replace('-', '') + '-' + run.lower() + '-controller',
                    'guard_unit': 'ops-' + case.lower().replace('-', '') + '-' + run.lower() + '-controller-guard',
                    'monitor_unit': 'ops-' + case.lower().replace('-', '') + '-' + run.lower() + '-controller-monitor',
                    'workload_seed': 31, 'start_utc': iso(self.now + 10),
                    'safety': 'NOFAULT_BASIC_HEALTH_PASS'}
        path = self.dir / (case + '.json')
        path.write_text(json.dumps(manifest, sort_keys=True) + '\n')
        return path, hashlib.sha256(path.read_bytes()).hexdigest(), manifest

    def test_five_case_manifests_and_old_path_rejection(self):
        for case in CASE_ACTIONS:
            with self.subTest(case=case):
                path, digest, _ = self.fixture(case)
                self.assertEqual(load_manifest(path, digest)['run_id'].split('-ATTEMPT-')[0], case)
        path, _, value = self.fixture('SF-014')
        value['actions'][0]['timer_command'] = '/root/ops-competition-2026/sf007-real-adapter-v7-20261018/scripts/fault_worker.py'
        path.write_text(json.dumps(value) + '\n')
        with self.assertRaisesRegex(ValueError, 'TIMER_OR_HTTP_COMMAND'):
            load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_missing_hash_and_unqualified_formal_rejected(self):
        path, _, value = self.fixture('SF-016')
        value['source_sha256'].pop(str(ROOT / 'scripts/formal_controller.py'))
        path.write_text(json.dumps(value) + '\n')
        with self.assertRaisesRegex(ValueError, 'SOURCE_HASH_MISSING'):
            load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())
        path, _, value = self.fixture('SF-016')
        value['mode'] = 'FORMAL_CANDIDATE'
        value['run_id'] = 'SF-016-ATTEMPT-03'
        value['controller_unit'] = 'ops-sf016-' + value['run_id'].lower() + '-controller'
        value['guard_unit'] = value['controller_unit'] + '-guard'
        value['monitor_unit'] = value['controller_unit'] + '-monitor'
        for action in value['actions']:
            action.update(run_id=value['run_id'],
                          action_id=value['run_id'] + '-' + action['kind'],
                          capability='BOUND_QUALIFIED_FAULT',
                          entrypoint=str(SHARED / 'worker_entry.py'))
            action['unit'] = 'ops-sf016-' + action['action_id'].lower()
        path.write_text(json.dumps(value) + '\n')
        with self.assertRaisesRegex(ValueError, 'NOT_QUALIFIED'):
            load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def sequence(self, case, io):
        _, _, manifest = self.fixture(case)
        for action in manifest['actions']:
            action['manifest_sha256'] = 'a' * 64
        cursor = [self.now]
        def sleep(seconds):
            cursor[0] += seconds
        return Sequence(manifest, io, clock=lambda: cursor[0], sleep=sleep).run(), io.events

    def test_all_five_harmless_sequences(self):
        for case in CASE_ACTIONS:
            with self.subTest(case=case):
                io = FakeIO(status='ACTION_TERMINAL')
                result, events = self.sequence(case, io)
                self.assertEqual(result['status'], 'CONTROL_SEQUENCE_COMPLETE_PENDING_ADMISSION')
                self.assertEqual(sum(name == 'permit' for name, _ in events),
                                 len(CASE_ACTIONS[case]))
                self.assertTrue(any(item.get('reason') == 'CONTROLLER_SUCCESS_FINALIZATION'
                    for item in result['cleanup'].values()))

    def test_sf017_failure_cancels_unissued_sibling(self):
        io = FakeIO(fail_at='HTTP')
        result, events = self.sequence('SF-017', io)
        self.assertEqual(result['status'], 'FAILED_CLOSED')
        self.assertNotIn(('permit', result['run_id'] + '-SERVICE'), events)
        self.assertIn(('revoke', result['run_id'] + '-SERVICE'), events)

    def test_guard_exit_and_remote_failure_fail_closed(self):
        io = FakeIO(guard=False)
        result, events = self.sequence('SF-013', io)
        self.assertEqual(result['status'], 'FAILED_CLOSED')
        self.assertFalse(any(name == 'permit' for name, _ in events))
        class BrokenRemote(FakeIO):
            def arm(self, action):
                raise ConnectionError('MASTER_TO_WORKER_CONTROL_LOST')
        result, events = self.sequence('SF-014', BrokenRemote())
        self.assertEqual(result['status'], 'FAILED_CLOSED')
        self.assertFalse(any(name == 'permit' for name, _ in events))
        self.assertTrue(any(name == 'revoke' for name, _ in events))

    def test_bound_timer_cancel_cannot_target_other_attempt(self):
        runtime = self.dir / 'worker-runtime'
        first = {'run_id': 'SF-014-ATTEMPT-MARKERTEST-ABCDEFGH',
                 'action_id': 'SF-014-ATTEMPT-MARKERTEST-ABCDEFGH-SERVICE',
                 'unit': 'ops-sf014-first'}
        second = {'run_id': 'SF-014-ATTEMPT-MARKERTEST-IJKLMNOP',
                  'action_id': 'SF-014-ATTEMPT-MARKERTEST-IJKLMNOP-SERVICE',
                  'unit': 'ops-sf014-second'}
        def no_units(command, **kwargs):
            if command[1] == 'show':
                return type('Process', (), {'returncode': 0,
                    'stdout': 'ActiveState=inactive\nLoadState=not-found\n'})()
            return type('Process', (), {'returncode': 0, 'stdout': ''})()
        self.assertEqual(cancel_timer(runtime, first, 'a' * 64,
                                      run=no_units)['status'], 'BOUND_TIMER_CANCELLED')
        with self.assertRaisesRegex(ValueError, 'OTHER_ATTEMPT'):
            cancel_timer(runtime, second, 'b' * 64, run=no_units)

    def test_emergency_cancel_uses_only_persisted_action_identity(self):
        run = 'SF-014-ATTEMPT-MARKERTEST-ABCDEFGH'
        action = run + '-SERVICE'
        root = self.dir / 'new-entry'
        runtime = root / 'runtime' / run / action
        runtime.mkdir(parents=True)
        identity = {'run_id': run, 'action_id': action,
                    'manifest_sha256': 'a' * 64}
        (runtime / 'auth-binding.json').write_text(json.dumps({
            **identity, 'worker_host': 'k8s-worker3'}))
        (runtime / 'cancel-binding.json').write_text(json.dumps({
            **identity, 'worker_host': 'k8s-worker3',
            'unit': 'ops-sf014-' + action.lower()}))
        with patch.object(worker_fault_entry, 'ROOT', root), \
             patch.object(worker_fault_entry.socket, 'gethostname', return_value='k8s-worker3'), \
             patch.object(worker_fault_entry, 'cancel_timer',
                          return_value={'status': 'BOUND_TIMER_CANCELLED'}) as cancel:
            result = worker_fault_entry.emergency_cancel(action, 'a' * 64)
            self.assertEqual(result['entry'], 'EMERGENCY_CANCEL_NO_MANIFEST_PARSE')
            cancel.assert_called_once()
            with self.assertRaisesRegex(ValueError, 'BINDING_MISMATCH'):
                worker_fault_entry.emergency_cancel(action, 'b' * 64)
            self.assertEqual(cancel.call_count, 1)

    def test_http_real_request_factory_revoke_and_controls(self):
        run = 'SF-015-ATTEMPT-03'
        action = {'run_id': run, 'action_id': run + '-HTTP',
                  'kind': 'HTTP', 'worker': 'k8s-master',
                  'mechanism': 'HTTPReqDelay', 'duration_seconds': 60,
                  'manifest_sha256': 'a' * 64,
                  'capability': 'BOUND_QUALIFIED_FAULT',
                  'entrypoint': str(SHARED / 'http_gate.py')}
        runtime = self.dir / 'http'
        runtime.mkdir()
        state = {'status': 'REGISTERED', 'run_id': run,
                 'action_id': action['action_id'], 'manifest_sha256': 'a' * 64,
                 'planned_start_epoch': self.now - 1,
                 'planned_end_epoch': self.now + 60,
                 'requester_pid': os.getpid(), 'requester_start_ticks': 99,
                 'controller_pid': os.getpid(), 'controller_start_ticks': 99,
                 'controller_unit': 'ops-sf015-controller',
                 'guard_unit': 'ops-sf015-guard'}
        (runtime / 'state.json').write_text(json.dumps(state))
        (runtime / 'permit.json').write_text(json.dumps({
            'status': 'PERMITTED', 'run_id': run,
            'action_id': action['action_id'], 'manifest_sha256': 'a' * 64,
            'issued_epoch': self.now - 2, 'expires_epoch': self.now + 60}))
        events = []
        factory = request_factory(action, runtime, urllib.request.Request,
            start=self.now - 1, end=self.now + 60, audit=events.append,
            now=lambda: self.now,
            units=lambda unit: {'ActiveState': 'active', 'MainPID': str(os.getpid())},
            ticks=lambda pid: 99)
        home = factory('http://example.test/', method='GET')
        cart = factory('http://example.test/cart', method='GET')
        self.assertEqual(home.get_header('X-envoy-fault-delay-request'), '250')
        self.assertIsNone(cart.get_header('X-envoy-fault-delay-request'))
        revoke(runtime, {'run_id': run, 'action_id': action['action_id'],
                         'manifest_sha256': 'a' * 64}, 'test')
        after = factory('http://example.test/', method='GET')
        self.assertIsNone(after.get_header('X-envoy-fault-delay-request'))
        self.assertEqual(events[-1]['status'], 'DENIED_NORMAL_REQUEST')

    def test_success_revoke_does_not_stop_normal_http_workload(self):
        run = 'SF-015-ATTEMPT-MARKERTEST-ABCDEFGH'
        action = {'run_id': run, 'action_id': run + '-HTTP',
                  'manifest_sha256': 'a' * 64, 'worker': 'k8s-master'}
        io = SystemIO.__new__(SystemIO)
        io.sha = action['manifest_sha256']
        with patch('formal_controller.revoke', return_value={'status': 'REVOKED'}), \
             patch('formal_controller.subprocess.run') as stop:
            io.revoke(action, 'CONTROLLER_SUCCESS_FINALIZATION')
            io.revoke(action, 'GUARD_VERIFIED_SUCCESS')
            stop.assert_not_called()
            io.revoke(action, 'INDEPENDENT_GUARD_TERMINAL')
            stop.assert_called_once()

    def test_storage_floor_blocks_cumulative_shortfall(self):
        free = {(host, mount): 20_000_000_000 for host, mounts in ROOTS.items()
                for mount in mounts}
        free[('k8s-worker3', '/data')] = 6_000_000_000
        result = storage_check('SF-017', free)
        self.assertEqual(result['status'], 'STORAGE_BLOCKED')
        self.assertIn('k8s-worker3:/data', result['shortfalls'])
        io = SystemIO.__new__(SystemIO)
        io.control = self.dir
        io.manifest = {'run_id': 'SF-017-ATTEMPT-03'}
        beat = {'status': 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS',
                'run_id': io.manifest['run_id'],
                'at_utc': iso(__import__('time').time()),
                'space': [{'host': host, 'mount': mount, 'free_bytes': count}
                          for (host, mount), count in free.items()]}
        (self.dir / 'safety-heartbeat.json').write_text(json.dumps(beat))
        with self.assertRaisesRegex(RuntimeError, 'DYNAMIC_STORAGE_BLOCKED'):
            io.live_heartbeat()

    def test_http_unit_budget_includes_predeployment_and_trace_tail(self):
        self.assertGreaterEqual(HTTP_RUNTIME_MAX_SECONDS, 180 + 1800 + 300)


if __name__ == '__main__':
    unittest.main()
