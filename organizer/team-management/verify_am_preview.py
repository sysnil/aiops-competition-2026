#!/usr/bin/env python3
"""Verify bounded AM QA preview from each assigned team machine."""

from __future__ import annotations

import json
import shlex
import subprocess


REMOTE = r'''
import json, pathlib, urllib.error, urllib.request
settings = dict(line.split('=', 1) for line in pathlib.Path('/etc/aiops-competition/team.env').read_text().splitlines() if '=' in line)
headers = {'Authorization': 'Bearer ' + settings['AIOPS_TEAM_TOKEN']}
def fetch(path):
    request = urllib.request.Request('http://192.168.51.51:8080' + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)
cases_status, cases = fetch('/v1/semifinal/cases')
questions_status, questions = fetch('/v1/semifinal/questions')
detail_status, detail = fetch('/v1/semifinal/questions/Q01')
metadata_status, metadata = fetch('/v1/semifinal/cases/SF-003/metadata')
practice_status, practice = fetch('/v1/practice/cases')
print(json.dumps({'team_id': settings['AIOPS_TEAM_ID'], 'cases_status': cases_status,
                  'cases': sorted(x['case_id'] for x in cases.get('items', [])),
                  'cases_error': cases.get('error', {}).get('code'),
                  'questions_status': questions_status,
                  'questions': sorted(x['question_id'] for x in questions.get('items', [])),
                  'preview_only': all(x.get('publication_status') == 'SEMIFINAL_PREVIEW' for x in questions.get('items', [])),
                  'detail_status': detail_status, 'metadata_status': metadata_status,
                  'practice_status': practice_status,
                  'practice_cases': sorted(x['case_id'] for x in practice.get('items', []))}))
'''


def main() -> None:
    expected_questions = [f'Q{i:02d}' for i in range(1, 11)]
    for number in range(1, 18):
        ip = f'192.168.51.{51 + number}'
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', f'root@{ip}',
                   'python3 -c ' + shlex.quote(REMOTE)]
        result = subprocess.run(command, text=True, capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError(f'{ip}: remote read-only check failed')
        row = json.loads(result.stdout.strip().splitlines()[-1])
        assert row['team_id'] == f'TEAM-{number:02d}', row
        assert row['practice_status'] == 200 and row['practice_cases'] == ['SF-001', 'SF-002'], row
        if number <= 10:
            assert row['cases_status'] == 200 and row['cases'] == ['SF-003', 'SF-005', 'SF-010'], row
            assert row['questions_status'] == 200 and row['questions'] == expected_questions, row
            assert row['preview_only'] and row['detail_status'] == 200 and row['metadata_status'] == 200, row
        else:
            assert row['cases_status'] == 403 and row['cases_error'] == 'SESSION_UNASSIGNED', row
            assert row['questions_status'] == 403 and row['detail_status'] == 403 and row['metadata_status'] == 403, row
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({'status': 'AM_PREVIEW_10_TEAMS_VERIFIED', 'am_teams': 10,
                      'other_teams_blocked': 7, 'questions': expected_questions}))


if __name__ == '__main__':
    main()
