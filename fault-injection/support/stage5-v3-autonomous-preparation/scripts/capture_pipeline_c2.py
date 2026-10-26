#!/usr/bin/env python3
"""Schedule live first reads, bounded trace completion, and lag checks locally."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path('/root/stage5-v3-autonomous-preparation')
SCRIPTS = ROOT / 'scripts'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
    if start.tzinfo is None or args.output.exists():
        raise ValueError('Pipeline needs a new output directory and timezone')
    args.output.mkdir(parents=True)
    scheduled = {}
    for phase, lo, hi in (('before', 0, 600), ('during', 600, 900), ('after', 900, 1500)):
        begin = (start + timedelta(seconds=lo)).isoformat()
        end = (start + timedelta(seconds=hi)).isoformat()
        first = args.output / 'captures' / phase
        commands = {
            'first-' + phase: [sys.executable, SCRIPTS / 'capture_live_c2.py',
                '--phase', phase, '--start-utc', begin, '--end-utc', end,
                '--trace-source', 'JAEGER_OPENSEARCH_STORAGE', '--wait-until-end',
                '--output', first],
            'completion-' + phase: [sys.executable, SCRIPTS / 'schedule_trace_completion.py',
                '--phase', phase, '--start', begin, '--end', end,
                '--first-capture', first, '--output', args.output / 'trace-completion' / phase],
            'lag-' + phase: [sys.executable, SCRIPTS / 'check_ingest_lag.py',
                '--start', begin, '--end', end,
                '--output', args.output / 'ingest-lag' / (phase + '.json')]}
        for name, command in commands.items():
            stdout = (args.output / (name + '.stdout')).open('x')
            stderr = (args.output / (name + '.stderr')).open('x')
            scheduled[name] = (subprocess.Popen([str(x) for x in command],
                                                stdout=stdout, stderr=stderr), stdout, stderr)
    status = {}
    try:
        for name, (process, stdout, stderr) in scheduled.items():
            try:
                status[name] = process.wait(timeout=1800)
            finally:
                stdout.close()
                stderr.close()
    finally:
        for process, stdout, stderr in scheduled.values():
            if process.poll() is None:
                process.terminate()
            if not stdout.closed:
                stdout.close()
            if not stderr.closed:
                stderr.close()
    result = {'status': 'CAPTURE_PIPELINE_PASS' if all(code == 0 for code in status.values())
              and len(status) == 9 else 'CAPTURE_PIPELINE_FAILED',
              'started_from_utc': start.isoformat(), 'completed_at_utc':
              datetime.now(timezone.utc).isoformat(), 'process_exit_codes': status}
    (args.output / 'pipeline-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    if result['status'] != 'CAPTURE_PIPELINE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
