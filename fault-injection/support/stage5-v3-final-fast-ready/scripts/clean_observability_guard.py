#!/usr/bin/env python3
"""Conservative external abort guard after a documented postrollback metric gap."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from clean_protocol import assert_disk, save_new, sha
from live_safety import tick
from postrollback_recovery_watch import metric, QUERIES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    schedule = json.loads((args.run / 'schedule.json').read_text())
    if schedule['run_id'] != 'S5V3FFR-CLEAN-20261013-S1' or schedule[
            'fault_injected']:
        raise RuntimeError('Wrong Clean source for independent guard')
    end = datetime.fromisoformat(schedule['scheduled_end_utc']).timestamp()
    output = args.run / 'external-observability-guard'
    if output.exists(): raise RuntimeError('Guard already exists')
    assert_disk(output, 5 * 1024 ** 3)
    output.mkdir()
    value = {'status': 'EXTERNAL_OBSERVABILITY_GUARD_FAILED', 'samples': 0,
        'run_id': schedule['run_id'], 'source_sha256': {
            str(Path(__file__)): sha(Path(__file__)),
            str(args.run / 'schedule.json'): sha(args.run / 'schedule.json')},
        'conservative_extra_guard_no_scoring_rule_change': True,
        'fault_injected': False}
    try:
        with (output / 'samples.jsonl').open('x') as stream:
            while time.time() <= end + 305:
                row = {'at_utc': datetime.now(timezone.utc).isoformat(),
                    'health': tick(),
                    'node_metrics': {name: metric(name) for name in QUERIES}}
                stream.write(json.dumps(row) + '\n'); stream.flush()
                value['samples'] += 1
                if (args.run / 'run-result.json').exists(): break
                time.sleep(30)
        value['status'] = 'EXTERNAL_OBSERVABILITY_GUARD_PASS'
    except Exception as error:
        value['error'] = str(error)
        subprocess.run(['systemctl', 'stop', 's5v3ffr-clean-s1.service'],
                       capture_output=True, text=True, timeout=20)
    finally:
        value['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(output / 'result.json', value)
        print(json.dumps({'status': value['status'], 'samples': value['samples'],
                          'error': value.get('error')}), flush=True)
    if value['status'] != 'EXTERNAL_OBSERVABILITY_GUARD_PASS':
        raise SystemExit(1)


if __name__ == '__main__': main()
