#!/usr/bin/env python3
"""Four immutable, deadline-bound reads of the issued no-fault Trace IDs."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from clean_protocol import assert_disk, save_new, sha


SOURCE = Path('/root/stage5-v3-collector-reliability/scripts/capture_continuous_traces.py')
STAGES = (('first_plus5', 5, 60), ('supplement_plus60', 60, 120),
          ('scoring_plus120', 120, 180), ('audit_plus240', 240, 300))


def source_module():
    spec = importlib.util.spec_from_file_location('capture_continuous', SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def issued(run):
    ids = []
    for path, sessions in ((run / 'state-machine/sessions.jsonl', True),
                           (run / 'controls/image_asset.jsonl', False),
                           (run / 'controls/cart_view.jsonl', False)):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            ids.extend(step['trace_id'] for step in (row['steps'] if sessions else [row])
                       if step.get('trace_id'))
    if not 8_000 <= len(ids) <= 40_000 or len(ids) != len(set(ids)):
        raise RuntimeError('Issued Trace ID denominator missing or duplicate')
    return ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    schedule = json.loads((args.run / 'schedule.json').read_text())
    if args.output.exists() or args.output != args.run / 'timed-traces' or \
            schedule['fault_injected'] or schedule['duration_seconds'] != 1800:
        raise RuntimeError('Trace schedule or immutable output invalid')
    assert_disk(args.output, 5 * 1024 ** 3)
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    end = datetime.fromisoformat(schedule['scheduled_end_utc']).timestamp()
    args.output.mkdir()
    result = {'status': 'TIMED_CAPTURE_FAIL', 'source': 'JAEGER_OPENSEARCH_STORAGE',
              'run_id': schedule['id'], 'stages': {}, 'fault_injected': False}
    previous, index_uuid = set(), None
    try:
        module = source_module()
        for name, offset, deadline in STAGES:
            time.sleep(max(0, end + offset - time.time()))
            begun = time.time()
            if not offset <= begun - end <= offset + 5:
                raise RuntimeError(name + ' missed start deadline')
            ids = issued(args.run)
            target = args.output / name
            target.mkdir()
            index = 'jaeger-jaeger-span-' + datetime.fromtimestamp(
                end, timezone.utc).strftime('%Y-%m-%d')
            spans, identities, source = module.capture(index, ids,
                int(start * 1_000_000) - 1_000_000,
                int(end * 1_000_000) + 5_000_000, target)
            completed = time.time()
            current = set(identities)
            if (completed - end > deadline or previous - current or
                (index_uuid and source['index_uuid'] != index_uuid) or
                source['expected_hits'] != source['actual_hits']):
                raise RuntimeError(name + ' missed completion, lost span, or changed source')
            index_uuid = source['index_uuid']
            summary = {'status': 'TRACE_STAGE_FROZEN', 'name': name,
                'start_lag_seconds': begun - end,
                'completion_lag_seconds': completed - end,
                'issued_trace_ids': len(ids), 'spans': len(current),
                'proxy_server_issued_trace_ids': len({s['traceID'] for s in spans
                    if s['process']['serviceName'] == 'frontend-proxy' and
                    (s.get('tag') or {}).get('span@kind') == 'server'} & set(ids)),
                'source': source, 'source_code_sha256': sha(SOURCE),
                'issued_sha256': hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest()}
            save_new(target / 'stage-manifest.json', summary)
            result['stages'][name] = {'status': summary['status'],
                'start_lag_seconds': summary['start_lag_seconds'],
                'completion_lag_seconds': summary['completion_lag_seconds'],
                'issued_trace_ids': len(ids), 'proxy_server_issued_trace_ids':
                    summary['proxy_server_issued_trace_ids'],
                'spans': summary['spans'],
                'manifest_sha256': sha(target / 'stage-manifest.json')}
            previous = current
        result['status'] = 'TIMED_TRACE_CAPTURE_PASS'
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        result['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(args.output / 'trace-schedule.json', result)
        print(json.dumps({'status': result['status'],
                          'stages': list(result['stages']),
                          'error': result.get('error')}), flush=True)


if __name__ == '__main__': main()
