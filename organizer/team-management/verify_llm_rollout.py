#!/usr/bin/env python3
"""Verify 17 LLM configs as the service user without exposing API keys."""

from __future__ import annotations

import json
import shlex
import subprocess


IPS = [f'192.168.51.{i}' for i in range(52, 69)]
REMOTE = r'''
import grp, hashlib, json, os, pathlib, subprocess, sys, urllib.request
from urllib.parse import urlsplit

path = pathlib.Path('/etc/aiops-competition/agent-llm.env')
group = grp.getgrnam('aiops-web')
info = path.lstat()
if not path.is_file() or path.is_symlink() or info.st_uid != 0 or info.st_gid != group.gr_gid or info.st_mode & 0o777 != 0o640:
    raise SystemExit('FILE_PERMISSIONS_INVALID')
project = '/opt/aiops-competition/participant-starter'
program = (
    'import hashlib,json,sys; from agent.llm_client import AgentLLMConfig; '
    'x=AgentLLMConfig.load("/etc/aiops-competition/agent-llm.env",{}); '
    'print(json.dumps({"base_url":x.base_url,"model":x.model,"temperature":x.temperature,'
    '"key_sha256":hashlib.sha256(x.api_key.encode()).hexdigest()}))'
)
result = subprocess.run(['runuser','-u','aiops-web','--',
                         '/opt/aiops-competition/.venv/bin/python','-c',program],
                        cwd=project, capture_output=True, text=True, timeout=10)
if result.returncode:
    raise SystemExit('SERVICE_USER_CANNOT_READ_CONFIG')
config = json.loads(result.stdout)
health = json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/health', timeout=5))
print(json.dumps({'config':config,'health':health.get('status'),
                  'team_token_configured':health.get('team_token_configured'),
                  'llm_configured':health.get('llm_configured')}))
'''


def main() -> None:
    results = []
    for index, ip in enumerate(IPS, 1):
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', f'root@{ip}',
                   'python3 -c ' + shlex.quote(REMOTE)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=25)
        if run.returncode:
            raise RuntimeError(f'{ip}: config validation failed')
        data = json.loads(run.stdout.strip().splitlines()[-1])
        config = data['config']
        if (config['base_url'] != 'https://yyt.s-flag.com/v1' or config['model'] != 'kimi-k3'
                or config['temperature'] != 1.0 or len(config['key_sha256']) != 64
                or data['health'] != 'ok' or data['team_token_configured'] is not True
                or data['llm_configured'] is not True):
            raise RuntimeError(f'{ip}: invalid model configuration or Web health')
        results.append(config['key_sha256'])
        print(json.dumps({'team': f'TEAM-{index:02d}', 'ip': ip, 'status': 'CONFIG_READABLE'}), flush=True)
    if len(set(results)) != len(IPS):
        raise RuntimeError('team API keys are not unique')
    print(json.dumps({'status': 'LLM_CONFIG_17_OF_17_VERIFIED', 'unique_keys': len(set(results)),
                      'model': 'kimi-k3', 'endpoint_host': 'yyt.s-flag.com'}))


if __name__ == '__main__':
    main()
