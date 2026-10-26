#!/usr/bin/env python3
"""Independent no-fault observer for a complete 1800-second window."""

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

FORMAL = Path('/root/ops-competition-2026/final-case-formal-entry-v1-20261019/scripts')
sys.path.insert(0, str(FORMAL))
from control_contract import load_manifest
from safety_monitor import sample


PROM = 'http://10.97.14.138:9090'
COUNTERS = ('otelcol_receiver_refused_spans_total',
            'otelcol_receiver_refused_log_records_total',
            'otelcol_receiver_refused_metric_points_total')


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def utc():
    return datetime.now(timezone.utc).isoformat()


def current_collector():
    data = json.loads(subprocess.check_output(
        ['kubectl', '-n', 'otel-demo', 'get', 'pods', '-o', 'json'],
        text=True, timeout=15))
    rows = [item for item in data['items'] if
            item['metadata']['name'].startswith('otel-collector-') and
            not item['metadata'].get('deletionTimestamp')]
    if len(rows) != 1 or not all(status.get('ready') for status in
                                 rows[0]['status'].get('containerStatuses', [])):
        raise RuntimeError('OBSERVER_COLLECTOR_NOT_UNIQUE_OR_READY')
    return rows[0]['metadata']['name'], rows[0]['metadata']['uid']


def query(expression):
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': expression})
    with urllib.request.urlopen(url, timeout=10) as response:
        body = json.load(response)
    if body.get('status') != 'success':
        raise RuntimeError('OBSERVER_PROMETHEUS_QUERY_FAILED')
    values = body['data']['result']
    if len(values) != 1:
        raise RuntimeError('OBSERVER_PROMETHEUS_SERIES_AMBIGUOUS')
    return float(values[0]['value'][1])


def counters(pod):
    return {name: query(f'sum({name}{{k8s_pod_name="{pod}"}})')
            for name in COUNTERS}


def run(manifest, output, *, interval=15):
    if output.exists():
        raise ValueError('OBSERVER_OUTPUT_IMMUTABLE')
    output.mkdir(parents=True, mode=0o700)
    name, uid = current_collector()
    initial = counters(name)
    end = stamp(manifest['window_end_utc'])
    count = 0
    result = {'status': 'OBSERVER_FAILED_CLOSED', 'run_id': manifest['run_id'],
              'collector_uid': uid, 'baseline_counters': initial}
    try:
        with (output / 'heartbeat.jsonl').open('x') as stream:
            while time.time() <= end + 5:
                observed_name, observed_uid = current_collector()
                if observed_name != name or observed_uid != uid:
                    raise RuntimeError('OBSERVER_COLLECTOR_IDENTITY_CHANGED')
                current = counters(name)
                if any(current[key] > initial[key] for key in COUNTERS):
                    raise RuntimeError('OBSERVER_COLLECTOR_REFUSED_INCREMENT')
                basic = sample(manifest)
                row = {'status': 'OBSERVER_HEARTBEAT_PASS', 'at_utc': utc(),
                       'sequence': count, 'collector_counters': current,
                       'basic_health': basic}
                stream.write(json.dumps(row, sort_keys=True) + '\n')
                stream.flush()
                count += 1
                time.sleep(min(interval, max(0, end + 5 - time.time())))
        result.update(status='OBSERVER_COMPLETE', samples=count,
                      final_counters=current, completed_utc=utc())
    except BaseException as error:
        result.update(error=type(error).__name__ + ':' + str(error),
                      samples=count, completed_utc=utc())
        raise
    finally:
        (output / 'terminal.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.manifest, args.sha256)
    if (manifest['mode'] != 'HARMLESS_REHEARSAL' or
            manifest.get('rehearsal_duration_seconds') != 1800):
        raise ValueError('OBSERVER_REHEARSAL_ONLY')
    print(json.dumps(run(manifest, args.output)), flush=True)


if __name__ == '__main__':
    main()
