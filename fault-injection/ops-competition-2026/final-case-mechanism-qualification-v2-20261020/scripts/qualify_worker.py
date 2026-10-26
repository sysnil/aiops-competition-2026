#!/usr/bin/env python3
"""Run one bounded v5 effect in a separate qualification, never a formal Case."""

import argparse
import importlib.util
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ADAPTER = OPS / 'sf013-adapter-v5-20261019/scripts/qualified_adapter.py'
QUAL_ROOT = OPS / 'final-case-mechanism-qualification-v2-20261020'
ID = re.compile(r'^QUAL-(SF-01[3-7])-(POD|SERVICE)(240|300)-V5-([A-Z0-9]{8})$')
ALLOWED = {
    ('SF-013', 'POD', 300): 'k8s-worker2',
    ('SF-014', 'SERVICE', 300): 'k8s-worker3',
    ('SF-016', 'POD', 300): 'k8s-worker2',
    ('SF-017', 'SERVICE', 240): 'k8s-worker3',
}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def validate(registration, authorization, output, *, now=time.time):
    reg = json.loads(registration.read_text())
    permit = json.loads(authorization.read_text())
    match = ID.fullmatch(reg.get('id', ''))
    if not match:
        raise ValueError('QUALIFICATION_ID_REQUIRED')
    case, layer, duration, _ = match.groups()
    if (reg.get('layer') != layer or reg.get('duration_seconds') != int(duration) or
            reg.get('host') != ALLOWED.get((case, layer, int(duration))) or
            reg['host'] != socket.gethostname()):
        raise ValueError('QUALIFICATION_MECHANISM_SCOPE')
    if (registration.is_symlink() or authorization.is_symlink() or
            output != OPS / 'qualification/private-attempts' / reg['id'] or
            output.exists() or
            authorization.parent != QUAL_ROOT / 'runs' / reg['id']):
        raise ValueError('QUALIFICATION_PRIVATE_PATH')
    import hashlib
    digest = hashlib.sha256(registration.read_bytes()).hexdigest()
    if (permit.get('status') != 'COMPETITION_PREFLIGHT_AUTHORIZED' or
            permit.get('purpose') != 'FINAL_CASE_V5_MECHANISM_QUALIFICATION' or
            permit.get('id') != reg['id'] or
            permit.get('registration_sha256') != digest or
            not 0 <= now() - stamp(permit['at_utc']) <= 10):
        raise ValueError('QUALIFICATION_AUTHORIZATION_STALE_OR_WRONG')
    return reg, permit


def run(registration, authorization, output):
    reg, permit = validate(registration, authorization, output)
    spec = importlib.util.spec_from_file_location('qualified_final_effect', ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    adapter.IDS[reg['id']] = (reg['layer'], reg['duration_seconds'])
    revoked = authorization.parent / 'REVOKED.json'

    def before_effect():
        if revoked.exists() or time.time() - stamp(permit['at_utc']) > 10:
            raise RuntimeError('QUALIFICATION_REVOKED_OR_AUTH_EXPIRED')

    return adapter.run(registration, authorization, output,
                       before_effect=before_effect)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', required=True, type=Path)
    parser.add_argument('--authorization', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = run(args.registration, args.authorization, args.output)
    print(json.dumps(result, sort_keys=True), flush=True)
    if (result['status'] != 'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' or
            result.get('cleanup', {}).get('status') !=
            'COMPETITION_CLEANUP_ACTIONS_VERIFIED'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
