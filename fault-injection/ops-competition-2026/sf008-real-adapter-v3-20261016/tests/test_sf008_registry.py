import importlib.util
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/run_registry.py'
SPEC = importlib.util.spec_from_file_location('sf008_real_registry', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FormalPodHttpRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'plans/SF-008-ATTEMPT-03'
        folder.mkdir(parents=True)
        self.plan = folder / 'plan.json'
        self.lock = folder / 'schedule-lock.json'
        self.manifest = folder / 'run-manifest.json'
        self.registration = folder / 'pod-registration.json'
        start = datetime.now(timezone.utc) + timedelta(minutes=3)
        targets = [{'service': 'frontend', 'uid': 'frontend-1',
                    'node': 'k8s-worker2'}]
        self.action = {'run_id': 'SF-008-ATTEMPT-03',
            'action_id': 'SF-008-ATTEMPT-03-POD',
            'unit': 'ops-sf008-sf-008-attempt-03-pod',
            'mechanism_id': 'POD', 'worker': 'k8s-worker2',
            'targets': targets, 'registration_file': str(self.plan),
            'backend': MODULE.BACKEND,
            'execute': 'RUN_PINNED_QUALIFIED_ADAPTER',
            'revoke': 'REVOKE_STOP_AND_PINNED_CLEANUP',
            'duration_seconds': 60, 'onset_tolerance_seconds': 2,
            'offset_seconds': 600,
            'planned_utc': (start + timedelta(seconds=600)).isoformat(),
            'qualified_registration': str(self.registration),
            'runtime': str(self.root / 'worker-runtime/POD')}
        self.data = {'id': 'SF-008-ATTEMPT-03', 'backend': MODULE.BACKEND,
            'formal_case': True, 'authorization_scope':
            'ONE_SF008_POD_MEMORY60_REQDELAY60_ONLY',
            'duration_seconds': 1800, 'start_utc': start.isoformat(),
            'request_delay': {'operation': 'home', 'offset_seconds': 960,
                              'duration_seconds': 60, 'delay_ms': 250,
                              'lease_path': '/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-03/control/http-reqdelay-lease.json'},
            'raw_root': '/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-03',
            'control_root': '/data/stage5-v3/ops-competition-2026/runs/SF-008-ATTEMPT-03/control',
            'targets_and_siblings': targets + [
                {'service': 'frontend', 'uid': 'frontend-2', 'node': 'k8s-worker1'},
                {'service': 'recommendation', 'uid': 'recommendation-1', 'node': 'k8s-worker3'},
                {'service': 'recommendation', 'uid': 'recommendation-2', 'node': 'k8s-worker3'}],
            'controller_unit': 'controller', 'observer_unit': 'observer',
            'actions': [self.action]}

    def build(self):
        self.registration.write_text(json.dumps({'id': self.action['action_id'],
            'targets': self.action['targets'], 'duration_seconds': 60,
            'layer': 'POD', 'host': 'k8s-worker2',
            'allocation_bytes': 32*1024**2}))
        self.action['qualified_registration_sha256'] = MODULE.sha(self.registration)
        self.plan.write_text(json.dumps(self.data))
        self.lock.write_text(json.dumps({'run_id': self.data['id'],
            'plan_sha256': MODULE.sha(self.plan), 'actions': [self.action]}))
        self.manifest.write_text(json.dumps({'schema': 'EXPLICIT_RUN_REGISTRY_V1',
            'run_id': self.data['id'], 'backend': MODULE.BACKEND,
            'plan_path': str(self.plan), 'plan_sha256': MODULE.sha(self.plan),
            'schedule_lock_path': str(self.lock),
            'schedule_lock_sha256': MODULE.sha(self.lock),
            'actions': [self.action], 'controller_unit': 'controller',
            'observer_unit': 'observer'}))
        return MODULE.load_bundle(self.manifest, MODULE.sha(self.manifest), root=self.root)

    def test_only_exact_pod_and_http_contract_passes(self):
        self.assertEqual(self.build()[1]['duration_seconds'], 1800)

    def test_wrong_http_duration_rejected(self):
        self.data['request_delay']['duration_seconds'] = 61
        with self.assertRaisesRegex(ValueError, 'HTTP_FAULT_CONTRACT'):
            self.build()

    def test_missing_observer_target_universe_rejected_before_execution(self):
        del self.data['targets_and_siblings']
        with self.assertRaisesRegex(ValueError, 'PUBLIC_CANDIDATE_UNIVERSE'):
            self.build()

    def test_wrong_worker_rejected(self):
        self.action['worker'] = 'k8s-worker3'
        with self.assertRaisesRegex(ValueError, 'ACTION_WORKER_BINDING'):
            self.build()

    def test_wrong_pod_duration_rejected(self):
        self.action['duration_seconds'] = 300
        with self.assertRaisesRegex(ValueError, 'QUALIFIED_FAULT_BOUNDS'):
            self.build()


if __name__ == '__main__':
    unittest.main()
