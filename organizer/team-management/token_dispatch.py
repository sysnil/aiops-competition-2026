#!/usr/bin/env python3
"""Root-only, fail-closed delivery of existing team tokens from 51 to team hosts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.security import derive_hash


REGISTRY = Path('/etc/aiops-contest/team_registry.json')
CREDENTIALS = Path('/root/aiops-team-credentials-20261015.json')
KNOWN_HOSTS = Path('/root/.ssh/known_hosts')
AUDIT_ROOT = Path('/var/lib/aiops-contest/team-token-delivery')
DEFAULT_REMOTE_PATH = Path('/etc/aiops-competition/team.env')
TEAM_ID = re.compile(r'^TEAM-(0[1-9]|1[0-7])$')


REMOTE_PROGRAM = r'''
import grp, hashlib, hmac, ipaddress, json, os, pathlib, pwd, shutil, subprocess, sys, time

def fail(code):
    print(json.dumps({'status':'BLOCKED','code':code}))
    raise SystemExit(2)

try:
    request = json.load(sys.stdin)
    ip = str(ipaddress.ip_address(request['expected_ip']))
    path = pathlib.Path(request['path'])
    parent = pathlib.Path('/etc/aiops-competition')
    if path.parent != parent or not path.name.endswith('.env') or path.name.startswith('.'):
        fail('REMOTE_PATH_NOT_ALLOWED')
    if not parent.is_dir() or parent.is_symlink() or path.is_symlink() or (path.exists() and not path.is_file()):
        fail('REMOTE_PATH_INVALID')
    interfaces = json.loads(subprocess.check_output(['ip','-j','-4','addr','show'], text=True, timeout=5))
    addresses = {item['local'] for interface in interfaces for item in interface.get('addr_info', []) if item.get('family') == 'inet'}
    if ip not in addresses:
        fail('HOST_IP_MISMATCH')
    group = grp.getgrnam('aiops-web')
    pwd.getpwnam('aiops-web')
    unit = subprocess.run(['systemctl','cat','aiops-agent-web.service'], capture_output=True, text=True, timeout=5)
    if unit.returncode != 0 or str(path) not in unit.stdout:
        fail('WEB_SERVICE_PATH_MISMATCH')
    existing = path.read_bytes() if path.is_file() else None
    expected = request['expected_sha256']
    if not isinstance(expected, str) or len(expected) != 64:
        fail('EXPECTED_HASH_INVALID')
    current = hashlib.sha256(existing).hexdigest() if existing is not None else None
    if existing is not None and current == expected:
        current_info = path.stat()
        if current_info.st_uid != 0 or current_info.st_gid != group.gr_gid or (current_info.st_mode & 0o777) != 0o640:
            fail('EXISTING_TOKEN_PERMISSIONS_INVALID')
    if request['action'] == 'probe':
        if existing is not None and current != expected and not request.get('rotate'):
            fail('EXISTING_TOKEN_DIFFERS')
        print(json.dumps({'status':'READY','existing_match':current == expected}))
        raise SystemExit(0)
    if request['action'] != 'install':
        fail('ACTION_INVALID')
    token = request.get('token')
    team_id = request.get('team_id')
    if not isinstance(token, str) or not isinstance(team_id, str):
        fail('TOKEN_PAYLOAD_INVALID')
    content = f'AIOPS_TEAM_ID={team_id}\nAIOPS_TEAM_TOKEN={token}\n'.encode()
    if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), expected):
        fail('TOKEN_PAYLOAD_HASH_MISMATCH')
    if current == expected:
        print(json.dumps({'status':'ALREADY_CURRENT','verified':True}))
        raise SystemExit(0)
    if existing is not None and not request.get('rotate'):
        fail('EXISTING_TOKEN_DIFFERS')
    if existing is not None:
        backup_root = parent / '.token-backups'
        backup_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(backup_root, 0o700)
        backup = backup_root / f'{path.name}.{int(time.time())}.{os.getpid()}.bak'
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(existing)
            handle.flush()
            os.fsync(handle.fileno())
    temporary = parent / f'.{path.name}.{os.getpid()}.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchown(fd, 0, group.gr_gid)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    saved = path.read_bytes()
    if hashlib.sha256(saved).hexdigest() != expected or (path.stat().st_mode & 0o777) != 0o640 or path.stat().st_gid != group.gr_gid:
        fail('POST_WRITE_VERIFY_FAILED')
    print(json.dumps({'status':'INSTALLED','verified':True}))
except SystemExit:
    raise
except Exception as exc:
    trace = sys.exc_info()[2]
    while trace.tb_next:
        trace = trace.tb_next
    print(json.dumps({'status':'BLOCKED', 'code':'REMOTE_PREFLIGHT_OR_INSTALL_FAILED',
                      'error_type':type(exc).__name__, 'line':trace.tb_lineno}))
    raise SystemExit(2)
'''


@dataclass(frozen=True)
class Target:
    team_id: str
    ip: str
    token_id: str
    token: str

    def public(self) -> dict[str, str]:
        return {'team_id': self.team_id, 'ip': self.ip, 'token_id': self.token_id}


class DispatchError(RuntimeError):
    def __init__(self, reason: str, results: list[dict]):
        super().__init__(reason)
        self.results = results


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_private_json(path: Path, *, maximum_mode: int, enforce_permissions: bool = True) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (enforce_permissions and (
            info.st_uid != 0 or (stat.S_IMODE(info.st_mode) & ~maximum_mode))):
        raise ValueError(f'private source ownership or mode invalid: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def targets_from_sources(registry: dict, bundle: dict,
                         selected_team: str | None = None) -> list[Target]:
    if any(str(row.get('team_id', '')).startswith('TEAM-')
           and not TEAM_ID.fullmatch(str(row.get('team_id', '')))
           for row in registry.get('teams', [])):
        raise ValueError('unrecognized team identifier in registry')
    records = [row for row in registry.get('teams', []) if TEAM_ID.fullmatch(str(row.get('team_id', '')))]
    if len(records) != 17:
        raise ValueError('expected exactly 17 registered team records')
    credentials = [row for row in bundle.get('credentials', []) if TEAM_ID.fullmatch(str(row.get('team_id', '')))]
    if selected_team is None and len(credentials) != len(records):
        raise ValueError('credential bundle does not cover all registered teams')
    by_team = {row['team_id']: row for row in credentials}
    if len(by_team) != len(credentials):
        raise ValueError('duplicate credential team')
    team_ids, ips, token_ids, hashes = set(), set(), set(), set()
    targets = []
    for record in records:
        team_id, ip, token_id = record['team_id'], str(record.get('assigned_ip') or ''), str(record.get('token_id') or '')
        if (record.get('token_status') != 'ACTIVE' or not ip or not token_id
                or team_id in team_ids or ip in ips or token_id in token_ids
                or record.get('token_hash') in hashes):
            raise ValueError(f'invalid or non-unique team mapping: {team_id}')
        try:
            if ipaddress.ip_address(ip).version != 4:
                raise ValueError('IPv4 required')
        except ValueError as exc:
            raise ValueError(f'invalid assigned IP for {team_id}') from exc
        team_ids.add(team_id); ips.add(ip); token_ids.add(token_id); hashes.add(record['token_hash'])
        if selected_team is not None and team_id != selected_team:
            continue
        credential = by_team.get(team_id)
        if not credential:
            raise ValueError(f'credential bundle does not contain {team_id}')
        parts = str(credential.get('token', '')).split('.', 2)
        if (len(parts) != 3 or parts[0] != 'team'
                or parts[1] != token_id or credential.get('token_id') != token_id
                or credential.get('assigned_ip') != ip
                or not hmac.compare_digest(derive_hash(parts[2], record['token_salt']), record['token_hash'])):
            raise ValueError(f'stale or mismatched credential for {team_id}')
        targets.append(Target(team_id, ip, token_id, credential['token']))
    return sorted(targets, key=lambda item: item.team_id)


def token_file_bytes(target: Target) -> bytes:
    return f'AIOPS_TEAM_ID={target.team_id}\nAIOPS_TEAM_TOKEN={target.token}\n'.encode()


def host_key_pinned(ip: str, known_hosts: Path) -> bool:
    if not known_hosts.is_file() or known_hosts.is_symlink():
        return False
    result = subprocess.run(['ssh-keygen', '-F', ip, '-f', str(known_hosts)],
                            capture_output=True, timeout=5)
    return result.returncode == 0


def ssh_call(target: Target, payload: dict, known_hosts: Path) -> dict:
    command = [
        'ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
        '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}',
        '-o', 'ConnectTimeout=5', f'root@{target.ip}',
        'python3 -c ' + shlex.quote(REMOTE_PROGRAM),
    ]
    completed = subprocess.run(command, input=json.dumps(payload), text=True,
                               capture_output=True, timeout=25)
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'{target.team_id}: remote call failed (exit {completed.returncode})') from exc
    if completed.returncode or result.get('status') == 'BLOCKED':
        diagnostic = f" ({result['error_type']} at remote line {result['line']})" if 'error_type' in result else ''
        raise RuntimeError(f'{target.team_id}: {result.get("code", "REMOTE_CALL_FAILED")}{diagnostic}')
    return result


def dispatch(targets: list[Target], remote_call: Callable[[Target, dict], dict],
             remote_path: Path, *, rotate: bool = False) -> list[dict]:
    probes = []
    for target in targets:
        payload = {'action': 'probe', 'expected_ip': target.ip,
                   'path': str(remote_path), 'rotate': rotate,
                   'expected_sha256': hashlib.sha256(token_file_bytes(target)).hexdigest()}
        probes.append(remote_call(target, payload))
    results = []
    for target, probe in zip(targets, probes):
        payload = {'action': 'install', 'team_id': target.team_id,
                   'expected_ip': target.ip, 'path': str(remote_path),
                   'rotate': rotate, 'token': target.token,
                   'expected_sha256': hashlib.sha256(token_file_bytes(target)).hexdigest()}
        try:
            result = remote_call(target, payload)
            results.append({**target.public(), 'status': result['status'],
                            'verified': bool(result.get('verified'))})
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            results.append({**target.public(), 'status': 'FAILED', 'verified': False})
            raise DispatchError(str(exc), results) from exc
    return results


def append_audit(root: Path, payload: dict) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / 'delivery-audit.jsonl'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description='Plan or securely deliver existing team tokens; never prints a token')
    parser.add_argument('--registry', type=Path, default=REGISTRY)
    parser.add_argument('--credentials', type=Path, default=CREDENTIALS)
    parser.add_argument('--known-hosts', type=Path, default=KNOWN_HOSTS)
    parser.add_argument('--remote-path', type=Path, default=DEFAULT_REMOTE_PATH)
    parser.add_argument('--audit-root', type=Path, default=AUDIT_ROOT)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--team', help='one registered team, e.g. TEAM-03')
    selection.add_argument('--all', action='store_true', help='all 17 registered teams')
    parser.add_argument('--apply', action='store_true', help='write tokens after every host passes preflight')
    parser.add_argument('--rotate', action='store_true', help='allow replacing an existing different token; backs it up remotely')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('run as root on the 51 organizer server')
    if (args.remote_path.parent != Path('/etc/aiops-competition')
            or not args.remote_path.name.endswith('.env') or args.remote_path.name.startswith('.')):
        raise SystemExit('remote path must be a non-hidden .env file under /etc/aiops-competition')
    registry = read_private_json(args.registry, maximum_mode=0o640)
    bundle = read_private_json(args.credentials, maximum_mode=0o600)
    targets = targets_from_sources(registry, bundle, selected_team=args.team)
    if args.team and len(targets) != 1:
        raise SystemExit('team is not in the validated registry')
    pinned = {target.team_id: host_key_pinned(target.ip, args.known_hosts) for target in targets}
    plan = {'status': 'READY_TO_PLAN', 'apply': args.apply, 'remote_path': str(args.remote_path),
            'registry_sha256': file_sha256(args.registry),
            'credential_bundle_sha256': file_sha256(args.credentials),
            'targets': [{**target.public(), 'host_key_pinned': pinned[target.team_id]} for target in targets]}
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not all(pinned.values()):
        blocked = {'status': 'BLOCKED_UNPINNED_HOST_KEYS',
                   'at_utc': datetime.now(timezone.utc).isoformat(),
                   'registry_sha256': plan['registry_sha256'],
                   'teams': [team for team, present in pinned.items() if not present]}
        append_audit(args.audit_root, blocked)
        print(json.dumps(blocked, ensure_ascii=False))
        return 2
    snapshot = (plan['registry_sha256'], plan['credential_bundle_sha256'])
    result = {'status': 'PARTIAL_OR_BLOCKED', 'at_utc': datetime.now(timezone.utc).isoformat(),
              'target_count': len(targets), 'registry_sha256': snapshot[0],
              'credential_bundle_sha256': snapshot[1], 'results': []}
    try:
        def call(target: Target, payload: dict) -> dict:
            if (file_sha256(args.registry), file_sha256(args.credentials)) != snapshot:
                raise RuntimeError('source files changed during delivery')
            return ssh_call(target, payload, args.known_hosts)
        result['results'] = dispatch(targets, call, args.remote_path, rotate=args.rotate)
        result['status'] = 'TOKEN_FILE_DELIVERY_VERIFIED'
    except DispatchError as exc:
        result['results'] = exc.results
        result['reason'] = str(exc)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        result['reason'] = str(exc)
    append_audit(args.audit_root, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'TOKEN_FILE_DELIVERY_VERIFIED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
