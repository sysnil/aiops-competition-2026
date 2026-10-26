#!/usr/bin/env python3
"""Deliver per-team LLM config from stdin through 51 without storing secrets on 51."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import urlsplit


REGISTRY = Path('/etc/aiops-contest/team_registry.json')
AUDIT = Path('/var/lib/aiops-contest/llm-key-delivery/audit.jsonl')
HOSTS = Path('/root/.ssh/known_hosts')
TEAM_RE = re.compile(r'TEAM-(0[1-9]|1[0-7])\Z')

REMOTE = r'''
import grp, hashlib, json, os, pathlib, subprocess, sys

def stop(code):
    print(json.dumps({'status': 'BLOCKED', 'code': code}))
    raise SystemExit(2)

request = json.load(sys.stdin)
path = pathlib.Path('/etc/aiops-competition/agent-llm.env')
parent = path.parent
if parent.is_symlink() or not parent.is_dir() or path.is_symlink():
    stop('PATH_INVALID')
addresses = json.loads(subprocess.check_output(['ip', '-j', '-4', 'addr', 'show'], text=True, timeout=5))
ips = {row['local'] for interface in addresses for row in interface.get('addr_info', []) if row.get('family') == 'inet'}
if request['ip'] not in ips:
    stop('HOST_IP_MISMATCH')
group = grp.getgrnam('aiops-web')
unit = subprocess.run(['systemctl', 'cat', 'aiops-agent-web.service'], capture_output=True, text=True, timeout=5)
if unit.returncode or str(path) not in unit.stdout:
    stop('WEB_CONFIG_PATH_MISMATCH')
if path.exists():
    if not path.is_file() or path.stat().st_uid != 0 or path.stat().st_gid != group.gr_gid or path.stat().st_mode & 0o777 != 0o640:
        stop('EXISTING_FILE_INVALID')
    current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
else:
    current_hash = None
expected = request.get('expected_sha256')
if request['action'] == 'probe':
    if current_hash is not None and current_hash != expected:
        stop('EXISTING_CONFIG_DIFFERS')
    print(json.dumps({'status': 'READY', 'already_current': current_hash == expected and current_hash is not None}))
    raise SystemExit(0)
if request['action'] != 'install':
    stop('ACTION_INVALID')
values = request.get('config')
if not isinstance(values, dict) or set(values) != {'AIOPS_AGENT_LLM_BASE_URL', 'AIOPS_AGENT_LLM_MODEL', 'AIOPS_AGENT_LLM_API_KEY', 'AIOPS_AGENT_LLM_TEMPERATURE'}:
    stop('CONFIG_FIELDS_INVALID')
text = ''.join(f'{name}={values[name]}\n' for name in ('AIOPS_AGENT_LLM_BASE_URL', 'AIOPS_AGENT_LLM_MODEL', 'AIOPS_AGENT_LLM_API_KEY', 'AIOPS_AGENT_LLM_TEMPERATURE'))
if hashlib.sha256(text.encode()).hexdigest() != expected:
    stop('CONFIG_HASH_MISMATCH')
if current_hash == expected:
    print(json.dumps({'status': 'ALREADY_CURRENT', 'verified': True, 'sha256': expected}))
    raise SystemExit(0)
if current_hash is not None:
    stop('EXISTING_CONFIG_DIFFERS')
temporary = parent / f'.agent-llm.env.{os.getpid()}.tmp'
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.fchown(fd, 0, group.gr_gid)
    os.fchmod(fd, 0o640)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(text.encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    temporary.unlink(missing_ok=True)
if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
    stop('READBACK_HASH_MISMATCH')
print(json.dumps({'status': 'INSTALLED', 'verified': True, 'sha256': expected}))
'''


def target(team_id: str) -> str:
    if not TEAM_RE.fullmatch(team_id):
        raise ValueError('invalid team identifier')
    registry = json.loads(REGISTRY.read_text())
    matches = [row for row in registry['teams'] if row.get('team_id') == team_id]
    if len(matches) != 1 or matches[0].get('token_status') != 'ACTIVE':
        raise ValueError('team is not uniquely registered and active')
    ip = f'192.168.51.{51 + int(team_id[-2:])}'
    if matches[0].get('assigned_ip') != ip:
        raise ValueError('team IP differs from registry')
    pinned = subprocess.run(['ssh-keygen', '-F', ip, '-f', str(HOSTS)],
                            capture_output=True, timeout=5)
    if pinned.returncode:
        raise ValueError('host key is not pinned')
    return ip


def validate(config: dict[str, str]) -> None:
    if set(config) != {'AIOPS_AGENT_LLM_BASE_URL', 'AIOPS_AGENT_LLM_MODEL', 'AIOPS_AGENT_LLM_API_KEY', 'AIOPS_AGENT_LLM_TEMPERATURE'}:
        raise ValueError('config fields differ from required set')
    if any(not isinstance(value, str) or not value or any(char in value for char in '\r\n\x00')
           for value in config.values()):
        raise ValueError('config value is empty or contains a line break')
    base = urlsplit(config['AIOPS_AGENT_LLM_BASE_URL'])
    if base.scheme != 'https' or not base.hostname or base.username or base.password or base.query or base.fragment:
        raise ValueError('model endpoint must be an HTTPS URL without credentials or query parameters')
    if not re.fullmatch(r'[A-Za-z0-9._:/@-]{2,128}', config['AIOPS_AGENT_LLM_MODEL']):
        raise ValueError('model identifier format invalid')
    if len(config['AIOPS_AGENT_LLM_API_KEY']) < 16 or any(char.isspace() for char in config['AIOPS_AGENT_LLM_API_KEY']):
        raise ValueError('API key format invalid')
    if config['AIOPS_AGENT_LLM_TEMPERATURE'] != '1':
        raise ValueError('kimi-k3 endpoint requires temperature=1')


def content(config: dict[str, str]) -> bytes:
    return ''.join(f'{name}={config[name]}\n' for name in (
        'AIOPS_AGENT_LLM_BASE_URL', 'AIOPS_AGENT_LLM_MODEL', 'AIOPS_AGENT_LLM_API_KEY', 'AIOPS_AGENT_LLM_TEMPERATURE',
    )).encode()


def call(ip: str, payload: dict) -> dict:
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5', f'root@{ip}',
               'python3 -c ' + shlex.quote(REMOTE)]
    result = subprocess.run(command, input=json.dumps(payload), text=True,
                            capture_output=True, timeout=25)
    try:
        response = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'{ip}: remote operation returned no structured status') from exc
    if result.returncode or response.get('status') == 'BLOCKED':
        raise RuntimeError(f'{ip}: {response.get("code", "REMOTE_CALL_FAILED")}')
    return response


def audit(payload: dict) -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(AUDIT.parent, 0o700)
    fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        stream.write(json.dumps(payload, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('team_id')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('run as root on 51')
    ip = target(args.team_id)
    request = json.load(sys.stdin)
    config = request.get('config')
    if not isinstance(config, dict):
        raise SystemExit('config must be supplied on stdin')
    validate(config)
    expected = hashlib.sha256(content(config)).hexdigest()
    if request.get('expected_sha256') != expected:
        raise SystemExit('source config digest mismatch')
    probe = call(ip, {'action': 'probe', 'ip': ip, 'expected_sha256': expected})
    if not args.apply:
        print(json.dumps({'team_id': args.team_id, 'ip': ip, 'status': probe['status'],
                          'already_current': probe['already_current']}))
        return
    response = call(ip, {'action': 'install', 'ip': ip, 'config': config, 'expected_sha256': expected})
    record = {'at_utc': datetime.now(timezone.utc).isoformat(), 'team_id': args.team_id,
              'ip': ip, 'status': response['status'], 'sha256': expected}
    audit(record)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
