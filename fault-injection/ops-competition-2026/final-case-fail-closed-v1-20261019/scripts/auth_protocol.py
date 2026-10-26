"""Attempt-bound, one-shot authorization shared by Marker and formal entry."""

import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization


VERSION = 'SF_FINAL_LIVE_AUTH_V1'
TTL_SECONDS = 1.2
ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{2,127}$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now_utc(at=None):
    return datetime.fromtimestamp(time.time() if at is None else at,
                                  timezone.utc).isoformat()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def request(run_id, action_id, manifest_sha256, capability,
            worker_host, worker_boot_id=None, at=None):
    if (not ID.fullmatch(run_id) or not ID.fullmatch(action_id) or
            not action_id.startswith(run_id + '-') or
            not HEX64.fullmatch(manifest_sha256) or
            capability not in ('MARKER_ONLY', 'BOUND_QUALIFIED_FAULT')):
        raise ValueError('LIVE_AUTH_REQUEST_IDENTITY')
    return {'version': VERSION, 'run_id': run_id, 'action_id': action_id,
            'manifest_sha256': manifest_sha256, 'capability': capability,
            'worker_host': worker_host,
            'worker_boot_id': worker_boot_id or boot_id(),
            'nonce': secrets.token_hex(32), 'requested_utc': now_utc(at)}


def validate_request(value, at=None):
    at = time.time() if at is None else at
    expected = {'version', 'run_id', 'action_id', 'manifest_sha256',
                'capability', 'worker_host', 'worker_boot_id', 'nonce',
                'requested_utc'}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('LIVE_AUTH_REQUEST_SCHEMA')
    if (value['version'] != VERSION or
            not ID.fullmatch(value['run_id']) or
            not ID.fullmatch(value['action_id']) or
            not value['action_id'].startswith(value['run_id'] + '-') or
            not HEX64.fullmatch(value['manifest_sha256']) or
            not HEX64.fullmatch(value['nonce']) or
            value['capability'] not in ('MARKER_ONLY', 'BOUND_QUALIFIED_FAULT') or
            value['worker_host'] != 'k8s-worker2' or
            not isinstance(value['worker_boot_id'], str) or
            len(value['worker_boot_id']) != 36 or
            abs(at - stamp(value['requested_utc'])) > 2):
        raise ValueError('LIVE_AUTH_REQUEST_SCOPE_OR_TIME')
    return value


def load_private(path):
    key = serialization.load_pem_private_key(Path(path).read_bytes(),
                                               password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError('MASTER_AUTH_SIGNING_KEY_TYPE')
    return key


def load_public(path):
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError('MASTER_AUTH_PUBLIC_KEY_TYPE')
    return key


def sign_grant(private_key, req, master_boot_id, at=None):
    at = time.time() if at is None else at
    payload = {'version': VERSION, 'decision': 'GRANTED',
               'run_id': req['run_id'], 'action_id': req['action_id'],
               'manifest_sha256': req['manifest_sha256'],
               'capability': req['capability'], 'worker_host': req['worker_host'],
               'worker_boot_id': req['worker_boot_id'],
               'master_boot_id': master_boot_id, 'nonce': req['nonce'],
               'request_sha256': digest(req), 'issued_utc': now_utc(at),
               'expires_utc': now_utc(at + TTL_SECONDS)}
    signature = private_key.sign(canonical(payload))
    return {'payload': payload,
            'signature': base64.b64encode(signature).decode('ascii')}


def verify_grant(public_key, response, req, expected_master_boot_id,
                 at=None, consumed=None):
    at = time.time() if at is None else at
    if not isinstance(response, dict) or set(response) != {'payload', 'signature'}:
        raise ValueError('LIVE_AUTH_RESPONSE_SCHEMA')
    payload = response['payload']
    if not isinstance(payload, dict):
        raise ValueError('LIVE_AUTH_RESPONSE_PAYLOAD')
    public_key.verify(base64.b64decode(response['signature'], validate=True),
                      canonical(payload))
    for key in ('run_id', 'action_id', 'manifest_sha256', 'capability',
                'worker_host', 'worker_boot_id', 'nonce'):
        if payload.get(key) != req[key]:
            raise ValueError('LIVE_AUTH_RESPONSE_IDENTITY_' + key)
    issued, expires = stamp(payload['issued_utc']), stamp(payload['expires_utc'])
    if (payload.get('version') != VERSION or payload.get('decision') != 'GRANTED' or
            payload.get('request_sha256') != digest(req) or
            payload.get('master_boot_id') != expected_master_boot_id or
            not issued - .2 <= at <= expires or
            not 0 < expires - issued <= TTL_SECONDS + .01):
        raise ValueError('LIVE_AUTH_RESPONSE_STALE_OR_REPLAYED')
    if consumed is not None:
        consumed_path = Path(consumed)
        consumed_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(consumed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, canonical({'nonce': req['nonce'],
                                    'response_sha256': digest(response),
                                    'consumed_utc': now_utc(at)}) + b'\n')
            os.fsync(fd)
        finally:
            os.close(fd)
    return payload
