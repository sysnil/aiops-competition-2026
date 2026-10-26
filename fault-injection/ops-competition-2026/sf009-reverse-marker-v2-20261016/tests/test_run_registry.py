import importlib.util
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


FILE = Path(__file__).resolve().parents[1] / 'scripts/run_registry.py'
SPEC = importlib.util.spec_from_file_location('sf009_marker_registry', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MarkerRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'marker-plans/SF009-REVERSE-MARKER-02'
        self.folder.mkdir(parents=True)
        self.plan = self.folder / 'plan.json'
        self.lock = self.folder / 'schedule-lock.json'
        self.manifest = self.folder / 'run-manifest.json'
        start = datetime.now(timezone.utc) + timedelta(minutes=3)
        self.actions = []
        for number, (offset, stage) in enumerate(
                ((600, 'HTTP_REQDELAY'), (960, 'SERVICE_PAUSE')), start=1):
            ident = f'SF009-REVERSE-MARKER-02-E{number:02d}'
            self.actions.append({'run_id': 'SF009-REVERSE-MARKER-02',
                'action_id': ident, 'worker': 'k8s-worker3',
                'unit': 'ops-sf009-marker-' + ident.lower(),
                'backend': MODULE.BACKEND, 'execute': 'WRITE_LOCAL_TIME_MARKER',
                'revoke': 'REVOKE_AND_CANCEL_MARKER', 'targets': [],
                'offset_seconds': offset, 'intended_stage': stage,
                'planned_utc': (start + timedelta(seconds=offset)).isoformat(),
                'duration_seconds': 3, 'onset_tolerance_seconds': 2,
                'registration_file': str(self.plan),
                'runtime': str(self.root / 'worker-runtime' / ident)})
        self.data = {'id': 'SF009-REVERSE-MARKER-02', 'backend': MODULE.BACKEND,
            'formal_case': False, 'business_fault_permission': False,
            'actions': self.actions, 'start_utc': start.isoformat(),
            'duration_seconds': 1800, 'controller_unit': 'controller',
            'observer_unit': 'observer'}

    def write_bundle(self):
        self.plan.write_text(json.dumps(self.data))
        self.lock.write_text(json.dumps({'run_id': self.data['id'],
            'plan_sha256': MODULE.sha(self.plan), 'actions': self.actions}))
        self.manifest.write_text(json.dumps({'schema': 'EXPLICIT_RUN_REGISTRY_V1',
            'run_id': self.data['id'], 'backend': MODULE.BACKEND,
            'plan_path': str(self.plan), 'plan_sha256': MODULE.sha(self.plan),
            'schedule_lock_path': str(self.lock),
            'schedule_lock_sha256': MODULE.sha(self.lock),
            'actions': self.actions, 'controller_unit': 'controller',
            'observer_unit': 'observer'}))
        return MODULE.load_bundle(self.manifest, MODULE.sha(self.manifest), root=self.root)

    def test_exact_two_event_plan_passes(self):
        manifest, plan = self.write_bundle()
        self.assertEqual(len(plan['actions']), 2)
        with self.assertRaisesRegex(ValueError, 'ACTION_NOT_ON_WORKER'):
            MODULE.action_for(manifest, self.actions[0]['action_id'], 'k8s-worker2')

    def test_wrong_time_is_rejected(self):
        self.actions[1]['offset_seconds'] = 959
        with self.assertRaisesRegex(ValueError, 'SF009_REVERSE_30MIN_SCHEDULE'):
            self.write_bundle()

    def test_service_first_is_rejected(self):
        self.actions[0]['intended_stage'] = 'SERVICE_PAUSE'
        with self.assertRaisesRegex(ValueError, 'SF009_REVERSE_30MIN_SCHEDULE'):
            self.write_bundle()

    def test_extra_event_is_rejected(self):
        self.actions.append(dict(self.actions[-1], action_id='EXTRA'))
        with self.assertRaisesRegex(ValueError, 'LOCK_PLAN_ACTION_BINDING'):
            self.write_bundle()


if __name__ == '__main__':
    unittest.main()
