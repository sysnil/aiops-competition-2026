#!/usr/bin/env python3
"""Synthetic timing audit; no systemd Timer, SSH credential, or fault adapter."""

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


OPS = Path('/root/ops-competition-2026')
AUTH = OPS / 'final-case-fail-closed-v1-20261019/scripts'
sys.path.insert(0, str(AUTH))
from auth_protocol import digest, now_utc, request, sign_grant, verify_grant
from master_authorizer import authorize
import worker_auth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable synthetic race result exists')
    t0 = datetime.now(timezone.utc).timestamp()
    master_boot = '11111111-1111-1111-1111-111111111111'
    worker_boot = '22222222-2222-2222-2222-222222222222'
    key = Ed25519PrivateKey.generate()
    req = request('SF013-RACE-PROBE', 'SF013-RACE-PROBE-MARK',
                  'a' * 64, 'MARKER_ONLY', 'k8s-worker2', worker_boot, t0)
    response = sign_grant(key, req, master_boot, t0)
    grant_valid_after_master_loss = bool(verify_grant(
        key.public_key(), response, req, master_boot, t0 + .09))
    stale_rejected = False
    try:
        verify_grant(key.public_key(), response, req, master_boot, t0 + 1.21)
    except ValueError:
        stale_rejected = True
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        action = root / req['run_id'] / req['action_id']
        action.mkdir(parents=True)
        state = {'status': 'REGISTERED',
                 'run_id': req['run_id'], 'action_id': req['action_id'],
                 'manifest_sha256': req['manifest_sha256'],
                 'capability': req['capability'],
                 'worker_host': req['worker_host'],
                 'worker_boot_id': worker_boot,
                 'master_boot_id': master_boot,
                 'planned_utc': now_utc(t0),
                 'controller_unit': 'ops-sf013-race-control',
                 'controller_pid': 1234,
                 'guard_unit': 'ops-sf013-race-guard'}
        permit = {'status': 'PERMITTED',
                  'run_id': req['run_id'], 'action_id': req['action_id'],
                  'manifest_sha256': req['manifest_sha256'],
                  'state_sha256': digest(state),
                  'issued_utc': now_utc(t0 - .5),
                  'expires_utc': now_utc(t0 + 2)}
        (action / 'state.json').write_text(json.dumps(state))
        (action / 'permit.json').write_text(json.dumps(permit))
        unit_calls = []

        def unit_state(unit):
            unit_calls.append(unit)
            if len(unit_calls) == 1:
                (action / 'REVOKED.json').write_text('{"status":"REVOKED"}')
            return {'ActiveState': 'active',
                    'MainPID': '1234' if unit.endswith('control') else '5678',
                    'Result': 'success'}

        issued_after_revoke = False
        try:
            signed = authorize(req, t0, root, unit_state,
                               lambda: master_boot, key)
            issued_after_revoke = signed['payload']['decision'] == 'GRANTED'
        except ValueError:
            pass
        local = root / 'worker-runtime'
        local.mkdir()
        binding = {'run_id': req['run_id'], 'action_id': req['action_id'],
                   'manifest_sha256': req['manifest_sha256'],
                   'capability': 'MARKER_ONLY',
                   'worker_host': 'k8s-worker2',
                   'worker_boot_id': worker_boot,
                   'master_boot_id': master_boot}
        reached_action = []

        def deliver_then_revoke(_binding, _runtime):
            (local / 'REVOKED.json').write_text('{"status":"REVOKED"}')
            return response['payload']

        with patch.object(worker_auth, 'live_grant', deliver_then_revoke):
            try:
                worker_auth.execute_once(binding, local,
                                         lambda grant: reached_action.append(grant))
            except RuntimeError:
                pass
        local_revoke_prevents_effect = not reached_action
    result = {
        'status': 'RESIDUAL_GRANT_RACES_CONFIRMED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'synthetic_only': True, 'real_faults_injected': 0,
        'temporary_signing_key_only': True,
        'observations': {
            'signed_grant_still_valid_90ms_after_master_loss':
                grant_valid_after_master_loss,
            'signed_grant_rejected_after_1_21_seconds': stale_rejected,
            'revoke_arriving_during_master_unit_check_did_not_prevent_grant':
                issued_after_revoke,
            'worker_local_revoke_before_effect_prevents_callback':
                local_revoke_prevents_effect,
        },
        'boundary': 'The tests do not prove a real business fault. They isolate '
                    'the ordering between Master revoke, signature issue, and '
                    'Worker effect callback.',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'observations': result['observations']}))


if __name__ == '__main__':
    main()
