"""Shared read-only plan shape required by the observer and registration gate."""


def validate_plan_fields(plan):
    expected = '/data/stage5-v3/ops-competition-2026/runs/SF-007-ATTEMPT-06'
    rows = plan.get('targets_and_siblings')
    if (plan.get('id') != 'SF-007-ATTEMPT-06' or
            plan.get('raw_root') != expected or
            plan.get('control_root') != expected + '/control' or
            not isinstance(rows, list) or len(rows) != 4 or
            not isinstance(plan.get('collector_uid'), str) or
            not plan['collector_uid']):
        raise ValueError('SF007_OBSERVER_PLAN_FIELDS')
    groups = {name: {row.get('uid') for row in rows if row.get('service') == name}
              for name in ('frontend', 'recommendation')}
    if any(len(uids) != 2 or None in uids for uids in groups.values()):
        raise ValueError('SF007_OBSERVER_CANDIDATE_UNIVERSE')
