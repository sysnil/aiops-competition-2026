#!/usr/bin/env python3
"""Verify team/IP authentication and 51-to-team file transfer without printing tokens."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


IPS = [f"192.168.51.{octet}" for octet in range(52, 69)]
REMOTE_CHECK = r'''
import json, pathlib, stat, urllib.error, urllib.request
ip, expected_team = EXPECTED_IP, EXPECTED_TEAM
path = pathlib.Path('/etc/aiops-competition/team.env')
content = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)
if content.get('AIOPS_TEAM_ID') != expected_team:
    raise SystemExit('TEAM_ID_MISMATCH')
if path.stat().st_mode & 0o777 != 0o640:
    raise SystemExit('TOKEN_FILE_MODE_INVALID')
request = urllib.request.Request('http://192.168.51.51:8080/v1/practice/cases',
                                 headers={'Authorization': 'Bearer ' + content['AIOPS_TEAM_TOKEN']})
try:
    with urllib.request.urlopen(request, timeout=8) as response:
        status = response.status
        body = json.load(response)
except urllib.error.HTTPError as error:
    status = error.code
    body = json.load(error)
web = json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=8))
print(json.dumps({'ip': ip, 'team_id': expected_team, 'http_status': status,
                  'api_error_code': body.get('error', {}).get('code') if isinstance(body.get('error'), dict) else body.get('code'),
                  'web_team_token_configured': web.get('team_token_configured'),
                  'web_status': web.get('status')}))
'''


def ssh(ip: str, command: str, *, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
                             '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
                             f'root@{ip}', command], input=input_bytes, capture_output=True,
                            timeout=20)
    if result.returncode:
        raise RuntimeError(f'{ip}: SSH verification failed ({result.returncode})')
    return result.stdout


def main() -> int:
    results = []
    for number, ip in enumerate(IPS, 1):
        team = f'TEAM-{number:02d}'
        program = f'EXPECTED_IP={ip!r}\nEXPECTED_TEAM={team!r}\n' + REMOTE_CHECK
        output = ssh(ip, 'python3 -c ' + __import__('shlex').quote(program))
        result = json.loads(output.decode().strip().splitlines()[-1])
        if (result['ip'] != ip or result['team_id'] != team
                or result['http_status'] != 403
                or result['api_error_code'] != 'PRACTICE_NOT_OPEN'
                or result['web_team_token_configured'] is not True
                or result['web_status'] != 'ok'):
            raise RuntimeError(f'{ip}: team/IP authentication or Web verification failed: {result}')
        results.append(result)
        print(json.dumps(result), flush=True)

    with tempfile.TemporaryDirectory(prefix='team-transfer-', dir='/root') as directory:
        source = Path(directory) / 'probe.txt'
        source.write_text('51-to-team-file-transfer-20261020\n')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        for ip in IPS:
            destination = '/tmp/aiops-team-transfer-probe-20261020.txt'
            ssh(ip, 'test ! -e ' + destination)
            copied = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
                                     '-o', 'StrictHostKeyChecking=yes', str(source),
                                     f'root@{ip}:{destination}'], capture_output=True, timeout=20)
            if copied.returncode:
                raise RuntimeError(f'{ip}: SCP upload failed ({copied.returncode})')
            program = (f'import hashlib,pathlib; p=pathlib.Path({destination!r}); '
                       f'assert hashlib.sha256(p.read_bytes()).hexdigest()=={digest!r}; p.unlink(); print("TRANSFER_VERIFIED")')
            if ssh(ip, 'python3 -c ' + __import__('shlex').quote(program)).strip() != b'TRANSFER_VERIFIED':
                raise RuntimeError(f'{ip}: SCP readback failed')
    print(json.dumps({'status': 'TEAM_IP_TOKEN_ACCESS_AND_SCP_VERIFIED', 'teams': len(results),
                      'practice_state': 'CLOSED', 'file_transfers': len(IPS)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
