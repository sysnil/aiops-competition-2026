"""Offline orchestration acceptance on the mounted disk; no live requests or faults."""

import json
from contextlib import suppress
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import uuid

import run_clean


MOUNT = Path('/data/stage5-v3')
FIXTURES = Path('/root/stage5-v3-final-fast-ready/tests/fixtures')


class Clock:
    def __init__(self): self.now = time.time()
    def time(self): return self.now
    def sleep(self, seconds): self.now += max(0, seconds)


class FakeProcess:
    def __init__(self, command, **kwargs):
        destination = Path(command[command.index('--output') + 1])
        if destination.name in ('state-machine', 'controls'):
            destination.mkdir(parents=True)
            status = 'LOAD_PASS' if destination.name == 'state-machine' else \
                'CONTROL_STREAMS_COMPLETE'
            (destination / 'result-manifest.json').write_text(json.dumps({'status': status}))
        self.destination = destination
    def poll(self): return None
    def wait(self, timeout=None): return 0
    def terminate(self): pass
    def kill(self): pass


@unittest.skipUnless(MOUNT.is_mount(), 'Master mounted-disk offline acceptance only')
class FullRunnerOfflineTests(unittest.TestCase):
    def setUp(self):
        self.id = 'S5V3FFR-CLEAN-OFFLINE-' + uuid.uuid4().hex[:12].upper()
        self.root = MOUNT / 'final-fast-ready/offline-tests'
        self.root.mkdir(parents=True, exist_ok=True)
        FIXTURES.mkdir(parents=True, exist_ok=True)
        plan = json.loads(Path('/root/stage5-v3-final-fast-ready/design/clean-prereg-s1.json').read_text())
        plan['id'] = self.id
        self.plan = FIXTURES / (self.id + '-plan.json')
        self.plan.write_text(json.dumps(plan))
        self.health = FIXTURES / (self.id + '-health.json')
        self.health.write_text(json.dumps({'status': 'GLOBAL_SAFETY_PASS',
            'captured_at_utc': run_clean.utc_now(), 'blockers': []}))
        self.residue = FIXTURES / (self.id + '-residue.json')
        self.residue.write_text(json.dumps({'status': 'HOST_RESIDUE_PASS',
            'at_utc': run_clean.utc_now()}))
        self.route = FIXTURES / (self.id + '-route.json')
        self.route.write_text(json.dumps({'status': 'ROUTE_FLOW_PROOF_PASS',
            'at_utc': run_clean.utc_now(),
            'target_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3'}))
        self.snapshot = {'pod_uids': {'recommendation': ['a', 'b'],
            'checkout': ['c'], 'cart': ['d']},
            'node_placement': {'checkout': [('c', 'k8s-worker2', '10.1.1.1')],
                               'cart': [('d', 'k8s-worker3', '10.2.2.2')]},
            'endpoint_slices_sha256': 'endpoints', 'route_proof_sha256': 'route',
            'live_route_sha256': 'live', 'collector_config_sha256': 'collector',
            'network_path': 'NETWORK_PATH/k8s-worker2/k8s-worker3'}

    def invoke(self, snapshot=None, code=0):
        clock = Clock()
        snapshot = snapshot or self.snapshot
        argv = ['run_clean.py', '--prereg', str(self.plan), '--health', str(self.health),
                '--residue', str(self.residue), '--route-proof', str(self.route)]
        with patch.object(sys, 'argv', argv), \
             patch.object(run_clean, 'DATA_ROOT', self.root), \
             patch.object(run_clean, 'assert_disk', return_value={'mount': {'uuid': 'test'}}), \
             patch.object(run_clean, 'check_health'), \
             patch.object(run_clean, 'validate_workload'), \
             patch.object(run_clean, 'safety_tick', return_value={'status': 'NO_FAULT_LIVE_SAFETY_PASS'}), \
             patch.object(run_clean, 'current_snapshot', side_effect=(snapshot if
                 callable(snapshot) else lambda _: snapshot)), \
             patch.object(run_clean.subprocess, 'Popen', side_effect=FakeProcess), \
             patch.object(run_clean.time, 'time', side_effect=clock.time), \
             patch.object(run_clean.time, 'sleep', side_effect=clock.sleep):
            with suppress(SystemExit):
                run_clean.main()
        return json.loads((self.root / 'runs' / self.id / 'run-result.json').read_text())

    def test_entire_no_fault_runner_sequence(self):
        result = self.invoke()
        self.assertEqual(result['status'],
            'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE')
        events = [json.loads(line)['stage'] for line in (self.root / 'runs' /
            self.id / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events[:2], ['PREFLIGHT', 'SOURCE_LOCK'])
        self.assertEqual(events.count('DURING'), 2)
        self.assertIn('TRACE_COMPLETION', events)
        self.assertFalse(result['fault_injected'])

    def test_output_conflict_fails_before_second_attempt(self):
        self.invoke()
        with self.assertRaises(RuntimeError): self.invoke()

    def test_source_lock_change_fails_closed(self):
        seen = 0
        def changed(_):
            nonlocal seen
            seen += 1
            value = self.snapshot.copy()
            if seen > 1:
                value['endpoint_slices_sha256'] = 'changed'
            return value
        result = self.invoke(snapshot=changed)
        self.assertEqual(result['status'], 'CLEAN_RUN_FAILED_CLOSED')
        self.assertIn('Source lock changed', result['error'])


if __name__ == '__main__': unittest.main()
