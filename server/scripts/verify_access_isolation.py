#!/usr/bin/env python3
"""Test all team credentials without revealing them or changing release state."""

import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request


BASE = 'http://192.168.51.51:8080'
STATE = Path('/etc/aiops-contest/competition_state.json')
CREDENTIALS = Path('/root/aiops-team-credentials-20261015.json')
PUBLIC = Path('/data/aiops-contest/public/manifests')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(path, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    call = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=8) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, body = error.code, error.read()
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        payload = {}
    return {'status': status, 'error_code': payload.get('error', {}).get('code')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Isolation result already exists')
    before = digest(STATE)
    state = json.loads(STATE.read_text())
    if (state['practice']['state'] != 'CLOSED' or
            state['semifinal']['published_cases'] or
            any(row['state'] != 'NOT_OPEN' for row in
                state['semifinal']['sessions'].values())):
        raise RuntimeError('Competition release state differs from authorized CLOSED/NOT_OPEN')
    credentials = json.loads(CREDENTIALS.read_text())['credentials']
    teams = [row for row in credentials if row['team_id'].startswith('TEAM-')]
    if len(teams) != 17:
        raise RuntimeError('Expected 17 distinct team credentials')
    checks = {}
    for row in teams:
        token = row['token']
        responses = {
            'practice_cases': request('/v1/practice/cases', token),
            'semifinal_cases': request('/v1/semifinal/cases', token),
            'legacy_sf003_metadata': request('/v1/cases/SF-003/metadata', token),
        }
        if any(value['status'] != 403 for value in responses.values()):
            raise RuntimeError('A team could access a closed Case route: ' + row['team_id'])
        checks[row['team_id']] = responses
    token = teams[0]['token']
    sample_id = json.loads((PUBLIC / 'SF-010.json').read_text())['sample_trace_id']
    extras = {
        'sf002_closed_metadata': request('/v1/practice/cases/SF-002/metadata', token),
        'sf003_closed_metadata': request('/v1/semifinal/cases/SF-003/metadata', token),
        'sf010_closed_trace': request('/v1/semifinal/cases/SF-010/traces/' + sample_id, token),
        'anonymous_sf003': request('/v1/semifinal/cases/SF-003/metadata'),
        'static_public_path': request('/public/cases/SF-003/public-v1/traces.parquet'),
        'healthz': request('/healthz'),
    }
    expected = {'sf002_closed_metadata': 403, 'sf003_closed_metadata': 403,
                'sf010_closed_trace': 403, 'anonymous_sf003': 401,
                'static_public_path': 404, 'healthz': 200}
    if any(extras[name]['status'] != status for name, status in expected.items()):
        raise RuntimeError('Closed or static route did not return expected status: ' + repr(extras))
    after = digest(STATE)
    if after != before:
        raise RuntimeError('Release state changed during read-only isolation test')
    result = {'status': 'SEVENTEEN_TEAM_CLOSED_ACCESS_PASS',
              'teams_tested': len(teams), 'team_checks': checks,
              'extra_checks': extras, 'release_state_sha256_before': before,
              'release_state_sha256_after': after,
              'tokens_recorded': False, 'submissions_created': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'teams_tested': len(teams),
                      'extra_checks': extras, 'release_state_sha256': before}))


if __name__ == '__main__':
    main()
