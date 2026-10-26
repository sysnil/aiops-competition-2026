"""Read-only Final qualifier standings, bounded by server receipt time."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = 'FINAL-QUALIFIER-BEST-MEAN-1.0'
SELECTION = 'HIGHEST_SCORE_EARLIEST_RECEIVED_WITHIN_WINDOW'
TIES = 'EARLIEST_DISPLAYED_MEAN_ACHIEVEMENT'
FAILURES = {'REJECTED', 'FAILED', 'VALIDATION_FAILED', 'ERROR', 'INFERENCE_FAILURE'}
HUNDREDTH = Decimal('0.01')


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('server receipt timestamp must include timezone')
    return result.astimezone(timezone.utc)


def elapsed_us(received, start):
    d = received - start
    return (d.days * 86400 + d.seconds) * 1_000_000 + d.microseconds


def elapsed_text(value):
    if value is None:
        return '—'
    seconds, microseconds = divmod(value, 1_000_000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{microseconds:06}'


def points(raw):
    return (raw * 10).quantize(HUNDREDTH, rounding=ROUND_HALF_UP)


def displayed_mean(values):
    return (sum(values, Decimal(0)) / 3).quantize(HUNDREDTH, rounding=ROUND_HALF_UP)


def validate_plan(plan):
    if (plan.get('status') != 'APPROVED_FOR_SCORING' or plan.get('selection') != SELECTION
            or plan.get('ties') != TIES or plan.get('aggregation') != 'THREE_TASK_MEAN'):
        raise ValueError('unsupported or unapproved Final qualifier scoring plan')
    if set(plan['tasks']) != {'FT-01', 'FT-02', 'FT-03'}:
        raise ValueError('exactly three qualifier tasks required')
    if len(plan['teams']) != 9 or len(set(plan['teams'])) != 9:
        raise ValueError('exactly nine distinct finalists required')
    if not plan.get('required_rubric_version') or not plan.get('required_gt_version'):
        raise ValueError('current rubric and GT version must be explicit')
    start, end = instant(plan['start_utc']), instant(plan['end_utc'])
    if start >= end:
        raise ValueError('invalid scoring interval')
    return start, end


def rank_rows(rows, plan, now=None):
    start, end = validate_plan(plan)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    now = now.astimezone(timezone.utc)
    teams, tasks = set(plan['teams']), plan['tasks']
    seen, selected, valid = set(), {}, []
    pending, attempts = Counter(), Counter()
    unresolved, audit = [], []
    for row in rows:
        sid = row['submission_id']
        if sid in seen:
            raise ValueError('duplicate submission ID in input')
        seen.add(sid)
        team, task = row.get('team_id'), row.get('task_id')
        entry = {k: row.get(k) for k in ('submission_id', 'team_id', 'task_id', 'case_id', 'status',
                                         'grading_mode', 'received_at', 'answer_sha256')}
        reason = None
        if team not in teams:
            reason = 'NOT_FINALIST'
        elif task not in tasks:
            reason = 'NOT_QUALIFIER_TASK'
        elif row.get('grading_mode') != 'OFFICIAL':
            reason = 'NOT_OFFICIAL'
        elif row.get('case_id') != tasks[task]:
            reason = 'TASK_CASE_MISMATCH'
            unresolved.append(sid)
        else:
            try:
                received = instant(row['received_at'])
            except (ValueError, TypeError, AttributeError):
                reason = 'INVALID_RECEIPT_TIME'
                unresolved.append(sid)
            else:
                if not start <= received < end:
                    reason = 'OUTSIDE_SCORING_WINDOW'
                elif received > now:
                    reason = 'FUTURE_RECEIPT_TIME'
                    unresolved.append(sid)
                else:
                    key = team, task
                    attempts[key] += 1
                    if row.get('status') != 'COMPLETED':
                        reason = 'NO_COMPLETED_SCORE'
                        if row.get('status') not in FAILURES:
                            pending[key] += 1
                            unresolved.append(sid)
                    else:
                        try:
                            grade = json.loads(row['grade_json']) if isinstance(row.get('grade_json'), str) else row.get('grade_json')
                            raw = grade['score']
                            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                                raise ValueError('not a numeric score')
                            score = Decimal(str(raw))
                            if not score.is_finite() or not 0 <= score <= 10:
                                raise ValueError('score outside 0..10')
                            if grade.get('status') != 'COMPLETED' or grade.get('grading_mode', 'OFFICIAL') != 'OFFICIAL':
                                raise ValueError('grade status/mode differs from official receipt')
                            if (grade.get('rubric_version') != plan['required_rubric_version']
                                    or grade.get('gt_version_identifier') != plan['required_gt_version']):
                                raise ValueError('score version mismatch')
                        except (KeyError, TypeError, ValueError, InvalidOperation):
                            reason = 'INVALID_OR_NONCURRENT_GRADE'
                            pending[key] += 1
                            unresolved.append(sid)
                        else:
                            entry.update(score_100=float(points(score)), raw_score=float(score),
                                         rubric_version=grade['rubric_version'], gt_version_identifier=grade['gt_version_identifier'])
                            candidate = {'row': row, 'raw': score, 'points': points(score), 'received': received}
                            valid.append(candidate)
                            previous = selected.get(key)
                            choice = (-score, received, sid)
                            if previous is None or choice < previous[0]:
                                selected[key] = choice, candidate
        entry['exclusion_reason'] = reason
        audit.append(entry)

    chosen = {v[1]['row']['submission_id'] for v in selected.values()}
    for entry in audit:
        entry['selected'] = entry['submission_id'] in chosen
        if entry['exclusion_reason'] is None and not entry['selected']:
            entry['exclusion_reason'] = 'NOT_BEST_OR_EARLIEST_BEST'

    leaderboard = []
    for team in plan['teams']:
        entry = {'team_id': team, 'maximum_score': 100, 'scored_tasks': 0,
                 'pending_count': sum(pending[team, t] for t in tasks)}
        scores = []
        for task, case in tasks.items():
            item = selected.get((team, task))
            candidate = item[1] if item else None
            entry[task] = float(candidate['points']) if candidate else None
            scores.append(candidate['points'] if candidate else Decimal(0))
            entry[task + '_case_id'] = case
            entry[task + '_submission_id'] = candidate['row']['submission_id'] if candidate else None
            entry[task + '_received_at_utc'] = candidate['received'].isoformat() if candidate else None
            entry[task + '_pending_count'] = pending[team, task]
            entry[task + '_attempt_count'] = attempts[team, task]
            entry[task + '_status'] = ('SCORED_PENDING_MORE' if pending[team, task] else 'SCORED') if candidate else (
                'PENDING' if pending[team, task] else 'NO_VALID_SCORE' if attempts[team, task] else 'NOT_SUBMITTED')
            entry['scored_tasks'] += int(candidate is not None)
        mean = displayed_mean(scores)
        entry['score_sum_300'] = float(sum(scores, Decimal(0)))
        entry['total_score'] = float(mean)
        # Find when the displayed average was first achieved. A later improvement
        # hidden by rounding must not delay the earlier achievement of that total.
        running = {task: Decimal(0) for task in tasks}
        achieved = None
        if mean > 0:
            for item in sorted((v for v in valid if v['row']['team_id'] == team),
                               key=lambda v: (v['received'], v['row']['submission_id'])):
                task = item['row']['task_id']
                running[task] = max(running[task], item['points'])
                if displayed_mean(running.values()) >= mean:
                    achieved = item['received']
                    break
        elapsed = elapsed_us(achieved, start) if achieved else None
        entry['total_score_achieved_at_utc'] = achieved.isoformat() if achieved else None
        entry['total_score_elapsed_microseconds'] = elapsed
        entry['total_score_elapsed_display'] = elapsed_text(elapsed)
        entry['speed_status'] = 'POSITIVE_TOTAL_ACHIEVED' if achieved else 'NO_POSITIVE_DISPLAYED_TOTAL'
        leaderboard.append(entry)

    def key(entry):
        elapsed = entry['total_score_elapsed_microseconds']
        return -Decimal(str(entry['total_score'])), elapsed if elapsed is not None else float('inf')
    leaderboard.sort(key=lambda entry: (*key(entry), entry['team_id']))
    for i, entry in enumerate(leaderboard):
        entry['rank'] = leaderboard[i - 1]['rank'] if i and key(entry) == key(leaderboard[i - 1]) else i + 1
    status = 'NOT_STARTED' if now < start else 'COMPLETE' if now >= end and not unresolved else 'PROVISIONAL'
    return {'status': status, 'ranking_version': VERSION, 'plan_version': plan['version'],
            'generated_at_utc': now.isoformat(), 'start_utc': start.isoformat(), 'end_utc': end.isoformat(),
            'time_rule': 'start <= server received_at < end', 'selection': SELECTION, 'ties': TIES,
            'aggregation': 'sum(per-task hundred-point best score, missing contributes 0) / 3; round to 2 decimals',
            'pending_or_unresolved_submission_ids': sorted(unresolved), 'leaderboard': leaderboard,
            'submission_audit': audit, 'selected_submission_count': len(chosen),
            'excluded_counts': dict(Counter(x['exclusion_reason'] for x in audit if x['exclusion_reason']))}
