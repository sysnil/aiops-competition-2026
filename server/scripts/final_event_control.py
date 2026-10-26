"""Root-only controller for the approved 2026-10-23 Final event."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.final_store import FinalStore, parse_time, final_window_visible
from app.storage import StorageError

ACTIVE_PK = ('READY', 'RUNNING', 'SUBMISSION_LOCKED', 'JUDGING')


class FinalEventControl:
    def __init__(self, database: Path, catalog: Path, plan: Path, registry: Path):
        self.plan = json.loads(plan.read_text())
        self.registry = registry
        self.store = FinalStore(database, catalog)
        self.mapping = self.plan['pk_tasks']
        self.actor = f'final-event-root:{os.getpid()}'

    def resolve(self, value: str) -> str:
        value = value.upper()
        if value in self.mapping:
            return value
        matches = [task for task, case in self.mapping.items() if case == value]
        if len(matches) != 1:
            raise ValueError('题号必须为 PK-R01–PK-R09 或对应 SF-xxx；先运行 aiops-final status')
        return matches[0]

    def team(self, value: str) -> str:
        match = re.fullmatch(r'(?:TEAM-?)?(\d{1,2})', value.upper())
        team = f'TEAM-{int(match[1]):02d}' if match else ''
        if team not in self.plan['finalists']:
            raise ValueError('队伍不在本届9支决赛名单中')
        return team

    def now(self):
        return datetime.now(timezone.utc)

    def status(self):
        now = self.now()
        return {
            'server_utc': now.isoformat(),
            'server_beijing': now.astimezone(timezone(timedelta(hours=8))).isoformat(),
            'morning': [{**t, 'effective_access': 'OPEN' if final_window_visible(t, now) else 'NOT_YET_OPEN_OR_CLOSED', 'opens_at_beijing': parse_time(t['opens_at']).astimezone(timezone(timedelta(hours=8))).isoformat() if t['opens_at'] else None} for t in self.store.tasks() if t['kind'] == 'FT'],
            'pk': self.store.rounds(),
            'pk_case_mapping': self.mapping,
            'finalists': self.plan['finalists'],
        }

    def open(self, case_or_round: str, first: str, second: str, minutes: int = 15):
        task = self.resolve(case_or_round)
        teams = self.team(first), self.team(second)
        if teams[0] == teams[1]:
            raise ValueError('必须指定两个不同队伍')
        if not 1 <= minutes <= 240:
            raise ValueError('每轮时长必须为1–240分钟；默认15分钟')
        if self.now() < parse_time(self.plan['morning_opens_at_utc']):
            raise ValueError('比赛日期尚未开始，禁止提前开放正式PK')
        records = {t['team_id']: t for t in json.loads(self.registry.read_text())['teams']}
        for team in teams:
            expected_ip = f'192.168.51.{51 + int(team[-2:])}'
            if (team not in records or records[team].get('assigned_ip') != expected_ip
                    or 'FINAL' not in records[team].get('allowed_modes', [])):
                raise ValueError(f'{team} 的FINAL授权或IP绑定不正确')
        opened = self.now()
        closed = opened + timedelta(minutes=minutes)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT round_id,status FROM final_rounds WHERE status IN (?,?,?,?)', ACTIVE_PK).fetchall()
            if active:
                raise ValueError(f'已有未结束PK：{active[0]["round_id"]}（{active[0]["status"]}）；先 close，再 result 或 cancel')
            row = db.execute('SELECT * FROM final_tasks WHERE task_id=?', (task,)).fetchone()
            round_row = db.execute('SELECT * FROM final_rounds WHERE round_id=?', (task,)).fetchone()
            if (not row or not round_row or row['case_id'] != self.mapping[task]
                    or row['simulation_only'] or round_row['simulation_only']):
                raise ValueError('正式题目绑定不一致，拒绝开题')
            if round_row['status'] not in ('NOT_OPEN', 'CLOSED', 'RESULT_CONFIRMED'):
                raise ValueError('本轮尚未结束；先 close，再 result 或 cancel')
            if round_row['opens_at'] and opened <= parse_time(round_row['closes_at']):
                raise ValueError('新轮次时间必须晚于上一轮结束时间')
            if round_row['status'] != 'NOT_OPEN':
                previous = db.execute(
                    "SELECT details_json,at FROM final_audit WHERE object_id=? "
                    "AND action='ROUND_RESULT_CONFIRMED' AND at>=? ORDER BY id DESC LIMIT 1",
                    (task, round_row['opens_at']),
                ).fetchone()
                submission_ids = [s[0] for s in db.execute(
                    'SELECT submission_id FROM final_submissions WHERE task_id=? '
                    'AND received_at>=? AND received_at<? ORDER BY received_at,submission_id',
                    (task, round_row['opens_at'], round_row['closes_at']),
                )]
                self.store._audit(db, self.actor, 'PK_SESSION_ARCHIVED', task, {
                    'round': dict(round_row), 'task': dict(row),
                    'submission_ids': submission_ids,
                    'confirmed_result': json.loads(previous['details_json']) if previous else None,
                    'confirmed_at': previous['at'] if previous else None,
                })
            # Both changes share one write transaction: concurrent commands cannot open two cases.
            db.execute('UPDATE final_rounds SET status=?,champion_team_id=?,challenger_team_id=?,opens_at=?,closes_at=?,winner_team_id=NULL WHERE round_id=?',
                       ('RUNNING', *teams, opened.isoformat(), closed.isoformat(), task))
            db.execute('UPDATE final_tasks SET status=?,opens_at=?,closes_at=? WHERE task_id=?',
                       ('OPEN', opened.isoformat(), closed.isoformat(), task))
            self.store._audit(db, self.actor, 'FORMAL_PK_OPENED', task,
                              {'case_id': self.mapping[task], 'teams': teams, 'minutes': minutes, 'session_opens_at': opened.isoformat(), 'reopened': round_row['status'] != 'NOT_OPEN'})
        return self.store.round(task)

    def close(self, case_or_round: str):
        task = self.resolve(case_or_round)
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM final_rounds WHERE round_id=?', (task,)).fetchone()
            if row['status'] not in ('RUNNING', 'SUBMISSION_LOCKED', 'RESULT_CONFIRMED', 'CLOSED'):
                raise ValueError('只能关闭已开始的PK')
            if row['status'] == 'RUNNING':
                # Closing must never extend the original submission deadline.
                closed = min(self.now(), parse_time(row['closes_at']))
                db.execute('UPDATE final_rounds SET status=?,closes_at=? WHERE round_id=?',
                           ('SUBMISSION_LOCKED', closed.isoformat(), task))
                db.execute('UPDATE final_tasks SET status=?,closes_at=? WHERE task_id=?',
                           ('SUBMISSION_LOCKED', closed.isoformat(), task))
                self.store._audit(db, self.actor, 'FORMAL_PK_CLOSED', task, {'closes_at': closed.isoformat()})
        try:
            result = self.result(task)
        except (ValueError, StorageError) as exc:
            # Stop accepting requests even if a real grade is still pending.
            return {**self.store.round(task), 'result_pending': True, 'reason': str(exc)}
        return {**self.store.round(task), 'result_pending': False, 'result': result}

    def result(self, case_or_round: str):
        task = self.resolve(case_or_round)
        row = self.store.round(task)
        if row['status'] in ('RESULT_CONFIRMED', 'CLOSED'):
            return self.display_result(self.store.confirmed_result(task))
        if row['status'] != 'SUBMISSION_LOCKED':
            raise ValueError('先 close 停止收件，再确认结果')
        with self.store._connect() as db:
            submissions = db.execute(
                'SELECT status,grade_json FROM final_submissions WHERE task_id=? AND grading_mode=? '
                'AND team_id IN (?,?) AND received_at>=? AND received_at<?',
                (task, 'OFFICIAL', row['champion_team_id'], row['challenger_team_id'],
                 row['opens_at'], row['closes_at']),
            ).fetchall()
            for sub in submissions:
                if sub['status'] not in ('COMPLETED', 'REJECTED', 'FAILED'):
                    raise ValueError('本轮仍有待评分提交，请稍后重新执行 result；不会将待评分当成0分')
                grade = json.loads(sub['grade_json'] or '{}')
                if sub['status'] == 'COMPLETED' and grade.get('rubric_version') != self.plan['rubric_version']:
                    raise ValueError('评分版本不一致，暂不确认胜者')
        self.store.confirm_round(task, self.actor)
        with self.store._connect() as db:
            db.execute('UPDATE final_tasks SET status=? WHERE task_id=? AND opens_at=? AND status=?',
                       ('SCORED' if self.store.round(task)['winner_team_id'] else 'CLOSED',
                        task, row['opens_at'], 'SUBMISSION_LOCKED'))
        return self.display_result(self.store.confirmed_result(task))

    @staticmethod
    def display_result(result):
        if result is None:
            return {'status': 'CLOSED_WITHOUT_WINNER'}
        result['display_totals'] = {k: round(v * 10, 2) for k, v in result['totals'].items()}
        result['display_max_score'] = 100
        return result

    def cancel(self, case_or_round: str, reason: str):
        task = self.resolve(case_or_round)
        if len(reason.strip()) < 4:
            raise ValueError('取消必须填写明确原因（至少4个字符）')
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM final_rounds WHERE round_id=?', (task,)).fetchone()
            if row['status'] == 'CLOSED' and row['winner_team_id'] is None:
                return dict(row)
            if row['status'] != 'SUBMISSION_LOCKED':
                raise ValueError('仅允许取消已关闭且尚未确认胜者的轮次')
            db.execute('UPDATE final_rounds SET status=? WHERE round_id=?', ('CLOSED', task))
            db.execute('UPDATE final_tasks SET status=? WHERE task_id=?', ('CLOSED', task))
            self.store._audit(db, self.actor, 'FORMAL_PK_CANCELLED_NO_WINNER', task, {'reason': reason.strip()})
        return self.store.round(task)


def main():
    parser = argparse.ArgumentParser(description='51组委会决赛控制：单题、双队、评分优先/同分快者胜')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('status', help='查看上半场和9道PK题状态')
    p = sub.add_parser('open', help='开放指定PK题给两个队伍，默认15分钟')
    p.add_argument('question', help='PK-R01–PK-R09 或 SF-xxx')
    p.add_argument('--teams', nargs=2, required=True, metavar=('TEAM_A', 'TEAM_B'))
    p.add_argument('--minutes', type=int, default=15)
    for name in ('close', 'result', 'cancel'):
        p = sub.add_parser(name)
        p.add_argument('question')
        if name == 'cancel':
            p.add_argument('--reason', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('请在51服务器使用root或sudo执行')
    ctl = FinalEventControl(Path('/var/lib/aiops-grading/submissions.sqlite3'),
                            Path('/opt/aiops-contest-server/config/final-stage1-catalog.json'),
                            Path('/etc/aiops-contest/final-event-20261023.json'),
                            Path('/etc/aiops-contest/team_registry.json'))
    try:
        if args.action == 'open':
            value = ctl.open(args.question, *args.teams, args.minutes)
        elif args.action == 'status':
            value = ctl.status()
        elif args.action == 'cancel':
            value = ctl.cancel(args.question, args.reason)
        else:
            value = getattr(ctl, args.action)(args.question)
    except (ValueError, StorageError) as exc:
        parser.exit(2, f'未执行：{exc}\n')
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
