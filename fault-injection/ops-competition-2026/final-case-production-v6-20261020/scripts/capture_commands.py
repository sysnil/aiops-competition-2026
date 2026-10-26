"""Three immutable trace audit stages for each formal 300-second chunk."""

from pathlib import Path


CAP = Path('/root/ops-competition-2026/low-stable-v7-20261020-capture')


def capture_commands(run, window):
    chunk = run / 'capture' / window['name']
    first = chunk / 'captures/during'
    middle = chunk / 'trace-completion/during'
    final = chunk / 'trace-final/during'
    late = chunk / 'trace-late-audit/during'
    start, end = window['start_utc'], window['end_utc']
    return {
        window['name'] + '-first': ['/usr/bin/python3', '-B',
            str(CAP / 'capture_live_c2.py'), '--phase', 'during',
            '--start-utc', start, '--end-utc', end,
            '--trace-source', 'JAEGER_OPENSEARCH_STORAGE',
            '--wait-until-end', '--output', str(first)],
        window['name'] + '-plus60': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_completion_v2.py'),
            '--phase', 'during', '--start', start, '--end', end,
            '--first-capture', str(first), '--output', str(middle)],
        window['name'] + '-plus120-plus240': ['/usr/bin/python3', '-B',
            str(CAP / 'schedule_trace_v2.py'), '--phase', 'during',
            '--start', start, '--end', end, '--first', str(first),
            '--plus60', str(middle), '--output', str(final),
            '--audit-output', str(late)],
    }
