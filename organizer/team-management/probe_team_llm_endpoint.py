#!/usr/bin/env python3
"""Make a minimal provider request from each team host; never print credentials."""

from __future__ import annotations

import json
import shlex
import subprocess


IPS = [f'192.168.51.{i}' for i in range(52, 69)]
REMOTE = r"""
import json, subprocess

program = '''import json, time
import httpx
from agent.llm_client import AgentLLMConfig

config = AgentLLMConfig.load('/etc/aiops-competition/agent-llm.env', {})
start = time.monotonic()
try:
    response = httpx.post(
        config.base_url.rstrip('/') + '/chat/completions',
        headers={'Authorization': 'Bearer ' + config.api_key, 'Content-Type': 'application/json'},
        json={'model': config.model, 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
              'max_tokens': 8, 'temperature': config.temperature},
        timeout=35,
    )
    valid = False
    if response.status_code == 200:
        payload = response.json()
        choices = payload.get('choices')
        valid = isinstance(choices, list) and bool(choices) and isinstance(choices[0].get('message', {}).get('content'), str)
    print(json.dumps({'http_status': response.status_code, 'response_shape_valid': valid,
                      'elapsed_seconds': round(time.monotonic() - start, 2)}))
except Exception as error:
    print(json.dumps({'http_status': None, 'response_shape_valid': False,
                      'error_type': type(error).__name__, 'elapsed_seconds': round(time.monotonic() - start, 2)}))
'''
result = subprocess.run(['runuser', '-u', 'aiops-web', '--',
                         '/opt/aiops-competition/.venv/bin/python', '-c', program],
                        cwd='/opt/aiops-competition/participant-starter',
                        capture_output=True, text=True, timeout=45)
if result.returncode:
    print(json.dumps({'http_status': None, 'response_shape_valid': False,
                      'error_type': 'TEAM_PROBE_FAILED'}))
else:
    print(result.stdout.strip())
"""


def main() -> None:
    failures = []
    for number, ip in enumerate(IPS, 1):
        args = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', f'root@{ip}',
                'python3 -c ' + shlex.quote(REMOTE)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=55)
        if result.returncode:
            status = {'http_status': None, 'response_shape_valid': False,
                      'error_type': 'SSH_FAILED'}
        else:
            try:
                status = json.loads(result.stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError):
                status = {'http_status': None, 'response_shape_valid': False,
                          'error_type': 'NO_STRUCTURED_REPLY'}
        ok = status['http_status'] == 200 and status['response_shape_valid'] is True
        if not ok:
            failures.append(f'TEAM-{number:02d}')
        print(json.dumps({'team': f'TEAM-{number:02d}', 'ip': ip, 'status': 'PASS' if ok else 'FAIL',
                          'http_status': status['http_status'], 'elapsed_seconds': status.get('elapsed_seconds'),
                          'error_type': status.get('error_type')}), flush=True)
    print(json.dumps({'status': 'ENDPOINT_17_OF_17_PASS' if not failures else 'ENDPOINT_PARTIAL',
                      'passed': len(IPS) - len(failures), 'failed_teams': failures}))
    if failures:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
