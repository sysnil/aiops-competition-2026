import importlib.util
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/run_registry.py'
SPEC = importlib.util.spec_from_file_location('sf004_registry', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HTTPOnlyRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'plans/SF-004-ATTEMPT-05'
        folder.mkdir(parents=True)
        self.plan_path = folder / 'run-plan.json'
        self.lock_path = folder / 'schedule-lock.json'
        self.manifest_path = folder / 'run-manifest.json'
        start = datetime.now(timezone.utc) + timedelta(minutes=4)
        case_root = '/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-05'
        self.plan = {'id': 'SF-004-ATTEMPT-05', 'backend': MODULE.BACKEND,
            'formal_case': True, 'authorization_scope':
                'ONE_SF004_HTTP_HOME_DELAY60_ONLY',
            'duration_seconds': 1800, 'start_utc': start.isoformat(),
            'raw_root': case_root, 'control_root': case_root + '/control',
            'targets_and_siblings': [
                {'service': 'frontend', 'uid': 'frontend-1'},
                {'service': 'frontend', 'uid': 'frontend-2'},
                {'service': 'recommendation', 'uid': 'recommendation-1'},
                {'service': 'recommendation', 'uid': 'recommendation-2'}],
            'request_delay': {'operation': 'home', 'offset_seconds': 900,
                              'duration_seconds': 60, 'delay_ms': 250,
                              'lease_path': case_root + '/control/http-reqdelay-lease.json'},
            'no_worker_timer': True, 'controller_unit': 'controller',
            'observer_unit': 'observer', 'actions': []}

    def build(self):
        self.plan_path.write_text(json.dumps(self.plan))
        self.lock_path.write_text(json.dumps({'run_id': self.plan['id'],
            'plan_sha256': MODULE.sha(self.plan_path), 'actions': []}))
        self.manifest_path.write_text(json.dumps({
            'schema': 'EXPLICIT_RUN_REGISTRY_V1', 'run_id': self.plan['id'],
            'backend': MODULE.BACKEND, 'plan_path': str(self.plan_path),
            'plan_sha256': MODULE.sha(self.plan_path),
            'schedule_lock_path': str(self.lock_path),
            'schedule_lock_sha256': MODULE.sha(self.lock_path),
            'actions': [], 'no_worker_timer': True,
            'controller_unit': 'controller', 'observer_unit': 'observer'}))
        return MODULE.load_bundle(self.manifest_path,
                                  MODULE.sha(self.manifest_path), root=self.root)

    def test_exact_http_only_scope_passes(self):
        self.assertEqual(self.build()[1]['request_delay']['offset_seconds'], 900)

    def test_worker_fault_capability_is_rejected(self):
        self.plan['actions'] = [{'mechanism_id': 'POD'}]
        with self.assertRaisesRegex(ValueError, 'WORKER_FAULT_NOT_AUTHORIZED'):
            self.build()

    def test_wrong_http_duration_is_rejected(self):
        self.plan['request_delay']['duration_seconds'] = 61
        with self.assertRaisesRegex(ValueError, 'HTTP_FAULT_CONTRACT'):
            self.build()

    def test_missing_public_candidate_metadata_is_rejected(self):
        self.plan['targets_and_siblings'] = []
        with self.assertRaisesRegex(ValueError, 'PUBLIC_CANDIDATE_UNIVERSE'):
            self.build()

    def test_worker_timer_flag_cannot_be_enabled(self):
        self.plan['no_worker_timer'] = False
        with self.assertRaisesRegex(ValueError, 'NO_WORKER_TIMER_SCOPE_REQUIRED'):
            self.build()


if __name__ == '__main__':
    unittest.main()
