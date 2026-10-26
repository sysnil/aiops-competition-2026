"""Synthetic temporary SQLite only; never opens a real competition round."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import app.final_store as module
from app.storage import StorageError
from final_event_control import FinalEventControl


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [datetime(2026, 10, 23, 6, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(module, 'utc_now', lambda: clock[0].isoformat())
    tasks = [dict(task_id='PK-R01', case_id='SF-004', kind='PK', max_submissions=1),
             dict(task_id='PK-R02', case_id='SF-008', kind='PK', max_submissions=1)]
    catalog=tmp_path/'catalog.json'
    catalog.write_text(json.dumps(dict(publication_state='INTERNAL_DEVELOPMENT_ONLY', tasks=tasks,
        preview_scoring=dict(round_winner_rule='ACCURACY_THEN_SERVER_RECEIPT_V1'))))
    plan=tmp_path/'plan.json'
    plan.write_text(json.dumps(dict(finalists=['TEAM-01','TEAM-02','TEAM-03'],
        morning_opens_at_utc='2026-10-23T01:00:00+00:00',rubric_version='test-rubric',
        pk_tasks={t['task_id']:t['case_id'] for t in tasks})))
    registry=tmp_path/'registry.json'
    registry.write_text(json.dumps(dict(teams=[dict(team_id=t, assigned_ip=f'192.168.51.{51+n}',
        allowed_modes=['FINAL']) for n,t in enumerate(['TEAM-01','TEAM-02','TEAM-03'],1)])))
    ctl=FinalEventControl(tmp_path/'test.sqlite3',catalog,plan,registry)
    ctl.now=lambda:clock[0]
    return ctl, clock


def submit(ctl, team, score=None, key='same-client-key-for-retry'):
    answer={'case_id':'SF-004','anomalies':[]}
    digest=hashlib.sha256(json.dumps(answer,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    r=ctl.store.create_submission('PK-R01',team,key,digest,answer,preview=False)
    if score is not None:
        ctl.store.set_grade(r['submission_id'],dict(status='COMPLETED',score=score,rubric_version='test-rubric'))
    return r


def close_cancel(ctl,clock):
    clock[0]+=timedelta(seconds=10)
    closed=ctl.close('PK-R01')
    if closed['status']=='SUBMISSION_LOCKED':ctl.cancel('PK-R01','synthetic test cancelled')
    clock[0]+=timedelta(seconds=1)


def test_cancelled_question_can_reopen_and_archive(env):
    ctl,clock=env
    old=ctl.open('PK-R01','team01','team02',1)
    close_cancel(ctl,clock)
    new=ctl.open('PK-R01','team02','team03',1)
    assert new['opens_at']!=old['opens_at'] and new['winner_team_id'] is None
    archived=[x for x in ctl.store.audit() if x['action']=='PK_SESSION_ARCHIVED']
    assert json.loads(archived[0]['details_json'])['round']['opens_at']==old['opens_at']


def test_new_open_has_new_quota_and_retry_scope(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    old=submit(ctl,'TEAM-01',8)
    assert submit(ctl,'TEAM-01')['submission_id']==old['submission_id']
    close_cancel(ctl,clock);ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    new=submit(ctl,'TEAM-01',5)
    assert old['submission_id']!=new['submission_id']
    assert submit(ctl,'TEAM-01')['submission_id']==new['submission_id']
    with pytest.raises(StorageError,match='submission limit'):
        submit(ctl,'TEAM-01',key='another-key-same-session')
    assert ctl.store.submission(old['submission_id'])['grade_json'] is not None


def test_new_result_ignores_old_winner_scores_and_pending(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    first=submit(ctl,'TEAM-01',9);clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-02',8)
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01')
    assert ctl.result('PK-R01')['winner']=='TEAM-01'
    clock[0]+=timedelta(seconds=1);ctl.open('PK-R01','team01','team02',1)
    assert ctl.store.confirmed_result('PK-R01') is None
    with ctl.store._connect() as db:
        db.execute("UPDATE final_submissions SET status='GRADE_PENDING',grade_json=NULL WHERE submission_id=?",(first['submission_id'],))
    clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-01',2)
    clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-02',7)
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01')
    result=ctl.result('PK-R01')
    assert result['winner']=='TEAM-02' and result['display_totals']['TEAM-01']==20
    assert ctl.result('PK-R01')==result
    assert len([x for x in ctl.store.audit() if x['action']=='ROUND_RESULT_CONFIRMED'])==2


def test_cancel_after_old_result_does_not_display_old_winner(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-01',5)
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01');ctl.result('PK-R01')
    clock[0]+=timedelta(seconds=1);ctl.open('PK-R01','team02','team03',1)
    close_cancel(ctl,clock)
    assert ctl.result('PK-R01')['winner'] is None
    assert ctl.result('PK-R01')['display_totals']=={'TEAM-02':0,'TEAM-03':0}


def test_running_or_locked_still_cannot_be_replaced(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1)
    for task in ('PK-R01','PK-R02'):
        with pytest.raises(ValueError,match='未结束PK'):
            ctl.open(task,'team02','team03',1)
    clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-01')
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01')
    with pytest.raises(ValueError,match='未结束PK'):
        ctl.open('PK-R01','team02','team03',1)


def test_new_pending_blocks_confirmation(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-01')
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01')
    with pytest.raises(ValueError,match='待评分'):
        ctl.result('PK-R01')


def test_reopened_round_only_accepts_new_participants(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);close_cancel(ctl,clock)
    ctl.open('PK-R01','team02','team03',1);clock[0]+=timedelta(seconds=1)
    with pytest.raises(StorageError,match='not open'):
        submit(ctl,'TEAM-01')
    assert submit(ctl,'TEAM-03',3)['team_id']=='TEAM-03'
