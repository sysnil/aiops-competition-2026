import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from auth_protocol import (boot_id, digest, request, sign_grant,
                           verify_grant, now_utc)
from master_authorizer import authorize


class LiveAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.at = 1792380000.0
        self.master_boot = '11111111-1111-1111-1111-111111111111'
        self.worker_boot = '22222222-2222-2222-2222-222222222222'
        self.key = Ed25519PrivateKey.generate()
        self.req = request('SF013-AUTH-UNIT', 'SF013-AUTH-UNIT-POD',
                           'a' * 64, 'MARKER_ONLY', 'k8s-worker2',
                           self.worker_boot, self.at)
        self.directory = self.root / self.req['run_id'] / self.req['action_id']
        self.directory.mkdir(parents=True)
        self.state = {
            'status': 'REGISTERED', 'run_id': self.req['run_id'],
            'action_id': self.req['action_id'],
            'manifest_sha256': self.req['manifest_sha256'],
            'capability': self.req['capability'],
            'worker_host': self.req['worker_host'],
            'worker_boot_id': self.worker_boot,
            'master_boot_id': self.master_boot,
            'planned_utc': now_utc(self.at),
            'controller_unit': 'ops-sf013-auth-test-control',
            'controller_pid': 1234,
            'guard_unit': 'ops-sf013-auth-test-guard'}
        self.permit = {
            'status': 'PERMITTED', 'run_id': self.req['run_id'],
            'action_id': self.req['action_id'],
            'manifest_sha256': self.req['manifest_sha256'],
            'state_sha256': digest(self.state),
            'issued_utc': now_utc(self.at - 1),
            'expires_utc': now_utc(self.at + 2)}
        self.save('state.json', self.state)
        self.save('permit.json', self.permit)

    def save(self, name, value):
        (self.directory / name).write_text(json.dumps(value))

    def unit(self, name):
        return {'ActiveState': 'active',
                'MainPID': '1234' if name.endswith('control') else '5678',
                'Result': 'success'}

    def grant(self, req=None, **kwargs):
        return authorize(req or self.req, at=self.at,
                         state_root=self.root, units=kwargs.get('units', self.unit),
                         current_boot=lambda: kwargs.get('boot', self.master_boot),
                         private_key=self.key)

    def test_normal_signed_one_shot_grant(self):
        response = self.grant()
        value = verify_grant(self.key.public_key(), response, self.req,
                             self.master_boot, self.at + .1,
                             self.directory / 'worker-consumed.json')
        self.assertEqual(value['decision'], 'GRANTED')
        self.assertTrue((self.directory / 'consumed.json').is_file())
        with self.assertRaises(FileExistsError):
            verify_grant(self.key.public_key(), response, self.req,
                         self.master_boot, self.at + .1,
                         self.directory / 'worker-consumed.json')
        with self.assertRaisesRegex(ValueError, 'ALREADY_CONSUMED'):
            self.grant()

    def test_master_rejects_revoked(self):
        self.save('REVOKED.json', {'status': 'REVOKED'})
        with self.assertRaisesRegex(ValueError, 'REVOKED'):
            self.grant()

    def test_master_rejects_controller_or_guard_exit(self):
        for unit in ('control', 'guard'):
            with self.subTest(unit=unit):
                def failed(name):
                    state = self.unit(name)
                    if name.endswith(unit):
                        state['ActiveState'] = 'inactive'
                    return state
                with self.assertRaisesRegex(ValueError, 'NOT_LIVE'):
                    self.grant(units=failed)

    def test_master_rejects_boot_change(self):
        with self.assertRaisesRegex(ValueError, 'CURRENT_PERMISSION_INVALID'):
            self.grant(boot='33333333-3333-3333-3333-333333333333')

    def test_master_rejects_action_manifest_or_worker_mismatch(self):
        for key, wrong in [('action_id', 'SF013-AUTH-UNIT-OTHER'),
                           ('manifest_sha256', 'b' * 64),
                           ('worker_boot_id', '33333333-3333-3333-3333-333333333333')]:
            with self.subTest(key=key):
                altered = {**self.req, key: wrong}
                with self.assertRaises(ValueError):
                    self.grant(altered)

    def test_master_rejects_expired_permission(self):
        self.permit['expires_utc'] = now_utc(self.at - .1)
        self.save('permit.json', self.permit)
        with self.assertRaisesRegex(ValueError, 'CURRENT_PERMISSION_INVALID'):
            self.grant()

    def test_response_replay_and_signature_tamper(self):
        response = sign_grant(self.key, self.req, self.master_boot, self.at)
        other = request(self.req['run_id'], self.req['action_id'],
                        self.req['manifest_sha256'], 'MARKER_ONLY',
                        'k8s-worker2', self.worker_boot, self.at)
        with self.assertRaises(ValueError):
            verify_grant(self.key.public_key(), response, other,
                         self.master_boot, self.at + .1)
        with self.assertRaises(ValueError):
            verify_grant(self.key.public_key(), response, self.req,
                         self.master_boot, self.at + 2)
        changed = json.loads(json.dumps(response))
        changed['payload']['decision'] = 'DENIED'
        with self.assertRaises(Exception):
            verify_grant(self.key.public_key(), changed, self.req,
                         self.master_boot, self.at + .1)


if __name__ == '__main__':
    unittest.main()
