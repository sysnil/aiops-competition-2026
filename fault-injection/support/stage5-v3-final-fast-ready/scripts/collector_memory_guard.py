#!/usr/bin/env python3
"""Independent correct-label Collector cgroup memory stop guard for one candidate run."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

from clean_protocol import assert_disk, save_new
from collector_candidate import old_module


PROM = 'http://10.97.14.138:9090'
LIMIT = 380 * 1024 ** 2


def sample(pod):
    expression = ('container_memory_working_set_bytes{namespace="otel-demo",'
                  'pod="' + pod + '",container="opentelemetry-collector"}')
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': expression})
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    rows = data['data']['result'] if data['status'] == 'success' else []
    if len(rows) != 1:
        raise RuntimeError('Collector container memory series missing or ambiguous')
    stamp, value = rows[0]['value']
    if not 0 <= time.time() - float(stamp) <= 45:
        raise RuntimeError('Collector memory scrape stale')
    return {'sample_timestamp': stamp, 'working_set_bytes': float(value),
            'labels': rows[0]['metric']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    schedule = json.loads((args.run / 'schedule.json').read_text())
    if schedule['id'] != 'S5V3FFR-COLLECTOR-20261013' or schedule['fault_injected']:
        raise RuntimeError('Wrong candidate source')
    output = args.run / 'independent-memory-guard'
    if output.exists():
        raise RuntimeError('Memory guard already exists')
    assert_disk(output, 5 * 1024 ** 3)
    output.mkdir()
    pod = schedule['gateway']['pod_name']
    end = datetime.fromisoformat(schedule['scheduled_end_utc']).timestamp()
    bad = count = 0
    result = {'status': 'INDEPENDENT_MEMORY_GUARD_FAILED', 'pod': pod,
              'limit_bytes': LIMIT, 'fault_injected': False}
    try:
        with (output / 'samples.jsonl').open('x') as stream:
            while time.time() <= end + 320:
                reading = sample(pod)
                reading['observed_utc'] = datetime.now(timezone.utc).isoformat()
                stream.write(json.dumps(reading) + '\n'); stream.flush()
                count += 1
                bad = bad + 1 if reading['working_set_bytes'] >= LIMIT else 0
                if bad >= 2:
                    raise RuntimeError('Collector memory above preregistered 380 MiB for two samples')
                if (args.run / 'result.json').exists():
                    break
                time.sleep(15)
        result['status'] = 'INDEPENDENT_MEMORY_GUARD_PASS'
    except Exception as error:
        result['reason'] = str(error)
        subprocess.run(['systemctl', 'stop', 's5v3ffr-collector-study.service'],
                       capture_output=True, text=True, timeout=20)
        plan = json.loads(args.plan.read_text())
        try:
            result['rollback'] = old_module().rollback(plan,
                output / 'emergency-rollback.json')
        except Exception as rollback_error:
            result['rollback_error'] = str(rollback_error)
    finally:
        result['samples'] = count
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(output / 'result.json', result)
        print(json.dumps(result), flush=True)
    if result['status'] != 'INDEPENDENT_MEMORY_GUARD_PASS':
        raise SystemExit(1)


if __name__ == '__main__': main()
