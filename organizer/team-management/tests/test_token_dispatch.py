from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from token_dispatch import (DispatchError, REMOTE_PROGRAM, dispatch, targets_from_sources)
from app.security import derive_hash


def sources():
    records, credentials = [], []
    for number in range(1, 18):
        team = f'TEAM-{number:02d}'
        ip = f'192.168.51.{number + 51}'
        token_id = f'TEAM{number:02d}-TEST'
        secret = f'synthetic-secret-{number}'
        salt = bytes([number] * 16).hex()
        records.append({'team_id': team, 'assigned_ip': ip,
                        'token_id': token_id, 'token_status': 'ACTIVE',
                        'token_salt': salt, 'token_hash': derive_hash(secret, salt)})
        credentials.append({'team_id': team, 'assigned_ip': ip,
                            'token_id': token_id,
                            'token': f'team.{token_id}.{secret}'})
    return {'teams': records}, {'credentials': credentials}


class TokenDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry, cls.bundle = sources()

    def test_one_to_one_mapping_and_stale_token_detection(self):
        tree = ast.parse(REMOTE_PROGRAM)
        imports = {alias.name for node in tree.body if isinstance(node, ast.Import)
                   for alias in node.names}
        self.assertIn('hmac', imports)
        targets = targets_from_sources(self.registry, self.bundle)
        self.assertEqual(len(targets), 17)
        self.assertEqual(len({target.ip for target in targets}), 17)
        self.assertEqual(len({target.token_id for target in targets}), 17)
        duplicate = {'teams': [dict(row) for row in self.registry['teams']]}
        duplicate['teams'][1]['assigned_ip'] = duplicate['teams'][0]['assigned_ip']
        with self.assertRaisesRegex(ValueError, 'non-unique'):
            targets_from_sources(duplicate, self.bundle)
        stale = {'credentials': [dict(row) for row in self.bundle['credentials']]}
        stale['credentials'][0]['token'] += '-stale'
        with self.assertRaisesRegex(ValueError, 'stale'):
            targets_from_sources(self.registry, stale)
        self.assertEqual(len(targets_from_sources(self.registry, stale,
                                                  selected_team='TEAM-02')), 1)

    def test_single_and_batch_probe_every_host_before_writing(self):
        selected = targets_from_sources(self.registry, self.bundle)[:2]
        calls = []

        def remote(target, payload):
            calls.append((target.team_id, payload['action'], 'token' in payload))
            return {'status': 'READY' if payload['action'] == 'probe' else 'INSTALLED',
                    'verified': payload['action'] == 'install'}

        result = dispatch(selected, remote, Path('/etc/aiops-competition/team.env'))
        self.assertEqual([item['status'] for item in result], ['INSTALLED', 'INSTALLED'])
        self.assertEqual([action for _, action, _ in calls],
                         ['probe', 'probe', 'install', 'install'])
        self.assertEqual([present for _, _, present in calls],
                         [False, False, True, True])
        calls.clear()
        self.assertEqual(len(dispatch(selected[:1], remote,
                                      Path('/etc/aiops-competition/team.env'))), 1)
        self.assertEqual([action for _, action, _ in calls], ['probe', 'install'])

    def test_preflight_failure_writes_no_token_and_partial_commit_is_auditable(self):
        selected = targets_from_sources(self.registry, self.bundle)[:2]
        calls = []

        def failed_preflight(target, payload):
            calls.append(payload['action'])
            if target == selected[1]:
                raise RuntimeError('HOST_IP_MISMATCH')
            return {'status': 'READY'}

        with self.assertRaises(RuntimeError):
            dispatch(selected, failed_preflight,
                     Path('/etc/aiops-competition/team.env'))
        self.assertEqual(calls, ['probe', 'probe'])

        def failed_second_commit(target, payload):
            if payload['action'] == 'probe':
                return {'status': 'READY'}
            if target == selected[1]:
                raise RuntimeError('REMOTE_WRITE_FAILED')
            return {'status': 'INSTALLED', 'verified': True}

        with self.assertRaises(DispatchError) as caught:
            dispatch(selected, failed_second_commit,
                     Path('/etc/aiops-competition/team.env'))
        self.assertEqual([item['status'] for item in caught.exception.results],
                         ['INSTALLED', 'FAILED'])
        self.assertNotIn('synthetic-secret', str(caught.exception.results))


if __name__ == '__main__':
    unittest.main()
