#!/usr/bin/env python3
"""Atomically open a bounded AM-only semifinal QA preview on 51."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path


REGISTRY = Path('/etc/aiops-contest/team_registry.json')
STATE = Path('/etc/aiops-contest/competition_state.json')
QUESTIONS = Path('/opt/aiops-contest-server/config/questions.json')
QA_PREVIEW = Path('/etc/aiops-private-grader/qa-gt-preview')
TEAMS = [f'TEAM-{i:02d}' for i in range(1, 11)]
CASES = ['SF-003', 'SF-005', 'SF-010']


def main() -> None:
    registry = json.loads(REGISTRY.read_text())
    teams = {row['team_id']: row for row in registry['teams'] if row.get('team_id') in TEAMS}
    if len(teams) != 10 or any(
        teams[team]['session'] != 'AM'
        or teams[team]['assigned_ip'] != f'192.168.51.{i + 51}'
        or teams[team]['token_status'] != 'ACTIVE'
        for i, team in enumerate(TEAMS, 1)
    ):
        raise SystemExit('AM_TEAM_MAPPING_INVALID')
    state = json.loads(STATE.read_text())
    semi = state['semifinal']
    if (semi.get('published_cases') != []
            or semi['sessions']['AM']['state'] != 'NOT_OPEN'
            or semi['sessions']['PM']['state'] != 'NOT_OPEN'
            or set(semi.get('internal_preview_cases', [])) != set(CASES)
            or semi.get('am_preview_test')):
        raise SystemExit('SEMIFINAL_STATE_CHANGED')
    questions = [row for row in json.loads(QUESTIONS.read_text())['questions']
                 if row.get('mode') == 'semifinal']
    if (len(questions) != 10 or {row['case_id'] for row in questions} != set(CASES)
            or any(row.get('publication_status') != 'SEMIFINAL_PREVIEW'
                   or not (QA_PREVIEW / f"{row['question_id']}.json").is_file()
                   for row in questions)):
        raise SystemExit('PREVIEW_QUESTIONS_OR_GT_INCOMPLETE')

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=48)
    semi['am_preview_test'] = {
        'enabled': True, 'classification': 'INTERNAL_TEAM_TEST_PREVIEW_ONLY',
        'teams': TEAMS, 'cases': CASES,
        'expires_at_utc': expires.isoformat().replace('+00:00', 'Z'),
    }
    semi['sessions']['AM'].update({
        'state': 'OPEN',
        'opens_at': now.isoformat().replace('+00:00', 'Z'),
        'closes_at': expires.isoformat().replace('+00:00', 'Z'),
    })
    state['updated_at'] = now.isoformat().replace('+00:00', 'Z')
    info = STATE.stat()
    temporary = STATE.with_name(STATE.name + '.am-preview-tmp')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, info.st_uid, info.st_gid)
        os.chmod(temporary, info.st_mode & 0o777)
        os.replace(temporary, STATE)
        directory = os.open(STATE.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'status': 'AM_PREVIEW_TEST_OPEN', 'teams': len(TEAMS),
                      'cases': CASES, 'expires_at_utc': semi['am_preview_test']['expires_at_utc']}))


if __name__ == '__main__':
    main()
