"""Exercise automatic close against temporary synthetic databases."""
import json
from datetime import timedelta
import pytest
from test_pk_reopen import env, submit, close_cancel


def test_both_missing_close_zero_no_winner_and_next_opens(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    closed=ctl.close('PK-R01')
    assert closed['status']=='CLOSED' and not closed['result_pending']
    result=closed['result']
    assert result['winner'] is None and result['display_totals']=={'TEAM-01':0,'TEAM-02':0}
    assert result['entries']['TEAM-01']['submission_status']=='NOT_SUBMITTED'
    assert result['entries']['TEAM-01']['submission_id'] is None
    assert result['entries']['TEAM-01']['received_at'] is None
    assert ctl.result('PK-R01')==result
    assert ctl.close('PK-R01')['result']==result
    with ctl.store._connect() as db:
        assert db.execute('select count(*) from final_submissions').fetchone()[0]==0
        assert db.execute("select count(*) from final_audit where action='ROUND_RESULT_CONFIRMED'").fetchone()[0]==1
    assert ctl.open('PK-R02','team02','team03',1)['status']=='RUNNING'


@pytest.mark.parametrize('score',[0,7.5])
def test_one_missing_zero_other_valid_wins_even_zero(env,score):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    submit(ctl,'TEAM-02',score);clock[0]+=timedelta(seconds=1)
    closed=ctl.close('PK-R01')
    assert closed['status']=='RESULT_CONFIRMED'
    assert closed['result']['winner']=='TEAM-02'
    assert closed['result']['display_totals']=={'TEAM-01':0,'TEAM-02':score*10}
    assert closed['result']['entries']['TEAM-01']['submission_status']=='NOT_SUBMITTED'
    assert ctl.open('PK-R02','team02','team03',1)['status']=='RUNNING'


def test_pending_is_not_zero_and_repeat_close_finishes_when_graded(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    row=submit(ctl,'TEAM-01');clock[0]+=timedelta(seconds=1)
    closed=ctl.close('PK-R01')
    assert closed['status']=='SUBMISSION_LOCKED' and closed['result_pending']
    assert ctl.store.confirmed_result('PK-R01') is None
    with pytest.raises(ValueError,match='未结束PK'):ctl.open('PK-R02','team02','team03',1)
    ctl.store.set_grade(row['submission_id'],dict(status='COMPLETED',score=4,rubric_version='test-rubric'))
    resolved=ctl.close('PK-R01')
    assert resolved['result']['winner']=='TEAM-01'
    assert resolved['result']['display_totals']['TEAM-01']==40


def test_old_result_does_not_supply_new_missing_scores(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1);submit(ctl,'TEAM-01',9)
    clock[0]+=timedelta(seconds=1);ctl.close('PK-R01')
    clock[0]+=timedelta(seconds=1);ctl.open('PK-R01','team01','team02',1)
    clock[0]+=timedelta(seconds=1);closed=ctl.close('PK-R01')
    assert closed['result']['display_totals']=={'TEAM-01':0,'TEAM-02':0}
    assert closed['winner_team_id'] is None


def test_close_after_expiry_keeps_original_deadline(env):
    ctl,clock=env
    opened=ctl.open('PK-R01','team01','team02',1)
    clock[0]+=timedelta(minutes=2)
    closed=ctl.close('PK-R01')
    assert closed['status']=='CLOSED' and closed['closes_at']==opened['closes_at']


def test_failed_submissions_keep_failure_status_zero_no_winner(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    row=submit(ctl,'TEAM-01')
    ctl.store.set_grade(row['submission_id'],dict(status='FAILED'))
    clock[0]+=timedelta(seconds=1);closed=ctl.close('PK-R01')
    assert closed['status']=='CLOSED'
    assert closed['result']['entries']['TEAM-01']['submission_status']=='FAILED'
    assert closed['result']['display_totals']=={'TEAM-01':0,'TEAM-02':0}


def test_wrong_rubric_waits_for_review_not_zero(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    row=submit(ctl,'TEAM-01')
    ctl.store.set_grade(row['submission_id'],dict(status='COMPLETED',score=8,rubric_version='wrong'))
    clock[0]+=timedelta(seconds=1);closed=ctl.close('PK-R01')
    assert closed['status']=='SUBMISSION_LOCKED' and closed['result_pending']
    assert ctl.store.confirmed_result('PK-R01') is None


def test_valid_exact_tie_does_not_invent_winner(env):
    ctl,clock=env
    ctl.open('PK-R01','team01','team02',1);clock[0]+=timedelta(seconds=1)
    submit(ctl,'TEAM-01',5);submit(ctl,'TEAM-02',5)
    clock[0]+=timedelta(seconds=1);closed=ctl.close('PK-R01')
    assert closed['status']=='SUBMISSION_LOCKED' and closed['result_pending']
    assert ctl.store.confirmed_result('PK-R01') is None
