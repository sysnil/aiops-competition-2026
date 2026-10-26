"""Prospective full-audit first-event gate; pure validation, no scheduling."""

from datetime import datetime


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def decide(schedule, quality, safety, collector_initial, collector_current,
           planned_start_utc, observed_utc, minimum_slack_seconds=15):
    reasons = []
    t0, onset, now = map(epoch, (schedule['start_utc'], planned_start_utc, observed_utc))
    if schedule.get('duration_seconds') != 1800 or schedule.get('faults_injected') not in (None, 0):
        reasons.append('NOT_A_FRESH_THIRTY_MINUTE_PLAN')
    if onset - t0 < 300 or onset >= t0 + 1800:
        reasons.append('CLEAN_BASELINE_NOT_COMPLETE')
    if quality is None or quality.get('status') != 'CHUNK_QUALITY_PASS' or quality.get('chunk') != 'chunk-00':
        reasons.append('FIRST_CHUNK_FULL_AUDIT_NOT_PASS')
    else:
        completed = epoch(quality['completed_utc'])
        if completed > now:
            reasons.append('FULL_AUDIT_RESULT_NOT_YET_OBSERVED')
        if completed > onset - minimum_slack_seconds:
            reasons.append('FULL_AUDIT_TOO_LATE_FOR_AUTHORIZATION')
        lags = quality.get('completion_lag_seconds', {})
        if not (240 <= lags.get('plus240', -1) <= 300):
            reasons.append('IMMUTABLE_PLUS240_AUDIT_MISSING')
    if now >= onset - 5:
        reasons.append('AUTHORIZATION_DEADLINE_MISSED')
    if safety.get('status') not in ('LIVE_ENVIRONMENT_PASS', 'COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING'):
        reasons.append('FRESH_SAFETY_NOT_PASS')
    if 'at_utc' not in safety or not 0 <= now - epoch(safety['at_utc']) <= 30:
        reasons.append('FRESH_SAFETY_AGE')
    if collector_current.get('refused') != collector_initial.get('refused'):
        reasons.append('COLLECTOR_REFUSAL_CHANGED')
    if 'at_utc' not in collector_current or not 0 <= now - epoch(collector_current['at_utc']) <= 30:
        reasons.append('COLLECTOR_STATE_STALE')
    return {'status': 'EARLY_FULL_AUDIT_CANDIDATE_PASS' if not reasons else 'EARLY_FULL_AUDIT_REJECT',
            'reasons': reasons, 'fault_authorization_issued': False,
            'cancel_pending_timer_on_reject': bool(reasons),
            'production_integration_validated': False}
