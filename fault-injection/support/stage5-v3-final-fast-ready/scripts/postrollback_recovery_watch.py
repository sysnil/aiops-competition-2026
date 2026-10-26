#!/usr/bin/env python3
"""Read-only 10-minute fresh recovery watch after Collector rollback."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

from clean_protocol import assert_disk, save_new
from live_safety import tick


OUTPUT = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-RECOVERY-WATCH-20261013')
PROM = 'http://10.97.14.138:9090'
QUERIES = ('k8s_node_cpu_usage', 'k8s_node_memory_available_bytes',
           'k8s_node_condition_ready')


def metric(name):
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': name})
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    rows = data['data']['result'] if data['status'] == 'success' else []
    result = {row['metric'].get('k8s_node_name'): {'sample_time': float(row[
        'value'][0]), 'value': float(row['value'][1])} for row in rows}
    if set(result) != {'k8s-master', 'k8s-worker1', 'k8s-worker2', 'k8s-worker3'}:
        raise RuntimeError(name + ' four-node series unavailable')
    if any(not 0 <= time.time() - row['sample_time'] <= 60 for row in result.values()):
        raise RuntimeError(name + ' samples stale')
    return result


def main():
    if OUTPUT.exists():
        raise ValueError('Recovery watch already exists')
    assert_disk(OUTPUT, 5 * 1024 ** 3)
    OUTPUT.mkdir()
    started = time.time()
    result = {'status': 'RECOVERY_WATCH_FAILED',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'samples': 0, 'fault_injected': False}
    try:
        with (OUTPUT / 'samples.jsonl').open('x') as stream:
            for number in range(21):
                planned = started + number * 30
                time.sleep(max(0, planned - time.time()))
                if time.time() - planned > 5:
                    raise RuntimeError('Recovery sampling schedule missed')
                health = tick()
                metrics = {name: metric(name) for name in QUERIES}
                row = {'planned_utc': datetime.fromtimestamp(planned,
                    timezone.utc).isoformat(), 'at_utc': datetime.now(
                    timezone.utc).isoformat(), 'health': health,
                    'metrics': metrics}
                stream.write(json.dumps(row) + '\n'); stream.flush()
                result['samples'] += 1
        result['status'] = 'POSTROLLBACK_FRESH_10MIN_OBSERVED'
    except Exception as error:
        result['error'] = str(error)
    finally:
        result['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(OUTPUT / 'result.json', result)
        print(json.dumps({'status': result['status'],
                          'samples': result['samples'],
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'POSTROLLBACK_FRESH_10MIN_OBSERVED':
        raise SystemExit(1)


if __name__ == '__main__': main()
