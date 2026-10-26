#!/usr/bin/env python3
"""Read historical competition control timestamps without touching GT."""

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path


OUT = Path(__file__).resolve().parents[1] / 'reports/historical-timing-evidence.json'
MASTER = ['ssh', '-S', '/tmp/sfgen-master.sock', '-o', 'BatchMode=yes',
          'root@192.168.53.52']


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def remote_json(path):
    command = shlex.join(['cat', path])
    process = subprocess.run(MASTER + [command], capture_output=True, text=True,
                             check=True, timeout=30)
    return json.loads(process.stdout)


def remote_lines(path):
    command = shlex.join(['cat', path])
    process = subprocess.run(MASTER + [command], capture_output=True, text=True,
                             check=True, timeout=30)
    return [json.loads(line) for line in process.stdout.splitlines()]


def main():
    if OUT.exists():
        raise RuntimeError('Do not overwrite a prior timing audit')
    base = '/data/stage5-v3/ops-competition-2026/'
    cases = [
        ('SF-001', base + 'runs/SF-PILOT-01-ATTEMPT-04',
         base + 'runtime/SF-PILOT-01-ATTEMPT-04'),
        ('SF-002', base + 'batch-sf002-sf009/SF-002',
         base + 'batch-sf002-sf009/runtime/SF-PILOT-02-ATTEMPT-01'),
    ]
    results = []
    for case_id, raw, control in cases:
        schedule = remote_json(raw + '/schedule.json')
        start = stamp(schedule['start_utc'])
        first = remote_json(raw + '/capture/chunk-00/quality.json')
        window_end = stamp(schedule['windows']['chunk-00']['end_utc'])
        workflow = remote_lines(control + '/workflow.jsonl')
        prechecks = []
        for index, row in enumerate(workflow):
            if row['phase'] != 'PREFLIGHT_BEGIN':
                continue
            tag = row.get('tag')
            end = next((candidate for candidate in workflow[index + 1:]
                        if candidate['phase'] == 'PREFLIGHT_PASS' and
                        candidate.get('tag') == tag), None)
            if end is not None:
                prechecks.append({'tag': tag,
                                  'duration_seconds': stamp(end['at_utc']) - stamp(row['at_utc'])})
        result = {
            'case_id': case_id,
            'first_chunk_end_offset_seconds': window_end - start,
            'first_chunk_final_quality_offset_seconds': stamp(first['completed_utc']) - start,
            'first_chunk_final_quality_after_window_end_seconds': stamp(first['completed_utc']) - window_end,
            'first_chunk_version_completion_lags_seconds': first['completion_lag_seconds'],
            'first_chunk_status': first['status'],
            'preflight_checks': prechecks,
            'first_fault_start_offset_seconds': min(
                stamp(row['at_utc']) - start for row in workflow if row['phase'] == 'ONSET_VERIFIED'),
        }
        results.append(result)
    payload = {'status': 'HISTORICAL_TIMING_READ_ONLY', 'cases': results,
               'not_an_authorization_to_shorten_recovery_or_fault_duration': True}
    OUT.write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'status': payload['status'],
                      'first_quality_offsets': [round(x['first_chunk_final_quality_offset_seconds'], 1)
                                                for x in results],
                      'max_preflight_seconds': max(x['duration_seconds'] for c in results
                                                    for x in c['preflight_checks'])}))


if __name__ == '__main__':
    main()
