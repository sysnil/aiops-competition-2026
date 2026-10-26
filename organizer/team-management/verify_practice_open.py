#!/usr/bin/env python3
"""Verify practice visibility and semifinal isolation from each team host."""

from __future__ import annotations

import json
import shlex
import subprocess


REMOTE = r'''
import json, pathlib, urllib.error, urllib.request
settings = dict(line.split('=', 1) for line in pathlib.Path('/etc/aiops-competition/team.env').read_text().splitlines() if '=' in line)
headers = {'Authorization': 'Bearer ' + settings['AIOPS_TEAM_TOKEN']}
def fetch(path):
    req = urllib.request.Request('http://192.168.51.51:8080' + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)
cases_status, cases = fetch('/v1/practice/cases')
questions_status, questions = fetch('/v1/practice/questions')
semi_status, semifinal = fetch('/v1/semifinal/cases')
print(json.dumps({'team_id': settings['AIOPS_TEAM_ID'], 'cases_status': cases_status,
                  'cases': sorted(x['case_id'] for x in cases.get('items', [])),
                  'questions_status': questions_status,
                  'questions': sorted(x['question_id'] for x in questions.get('items', [])),
                  'semifinal_status': semi_status,
                  'semifinal_error': semifinal.get('error', {}).get('code')}))
'''


def main() -> int:
    results = []
    for number in range(1, 18):
        ip = f'192.168.51.{number + 51}'
        team = f'TEAM-{number:02d}'
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'PasswordAuthentication=no',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
                   f'root@{ip}', 'python3 -c ' + shlex.quote(REMOTE)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise RuntimeError(f'{team}: remote verification failed ({run.returncode})')
        result = json.loads(run.stdout.strip().splitlines()[-1])
        if (result['team_id'] != team or result['cases_status'] != 200
                or result['cases'] != ['SF-001', 'SF-002']
                or result['questions_status'] != 200
                or not {'EX-P01', 'EX-P02'}.issubset(result['questions'])
                or result['semifinal_status'] != 403
                or result['semifinal_error'] != ('SESSION_NOT_OPEN' if number <= 10 else 'SESSION_UNASSIGNED')):
            raise RuntimeError(f'{team}: unexpected access result: {result}')
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    print(json.dumps({'status': 'ALL_TEAM_PRACTICE_OPEN_VERIFIED', 'teams': len(results),
                      'cases': ['SF-001', 'SF-002'], 'semifinal': 'NOT_OPEN'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
