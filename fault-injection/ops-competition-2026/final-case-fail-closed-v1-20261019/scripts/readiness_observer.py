#!/usr/bin/env python3
"""Independent no-fault monitor of the 1800-second capture process."""

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--collector-uid', required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.capture_root.is_dir():
        raise RuntimeError('Observer output must be new and capture initialized')
    schedule = read(args.capture_root / 'schedule.json')
    if (schedule['duration_seconds'] != 1800 or
            schedule['collector']['metadata']['uid'] != args.collector_uid or
            schedule['faults_injected'] != 0):
        raise RuntimeError('No-fault capture schedule/collector mismatch')
    args.output.mkdir(parents=True, mode=0o700)
    ready = args.output / 'ready.json'
    with ready.open('x') as stream:
        json.dump({'status': 'NOFAULT_OBSERVER_READY', 'at_utc': now(),
                   'collector_uid': args.collector_uid,
                   'capture_start_utc': schedule['start_utc']}, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    ready.chmod(0o600)
    result = {'status': 'NOFAULT_OBSERVER_FAILED', 'samples': 0,
              'collector_uid': args.collector_uid, 'faults_injected': 0}
    deadline = stamp(schedule['end_utc']) + 360
    monitor = args.capture_root / 'monitor.jsonl'
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda number, frame:
                      (_ for _ in ()).throw(SystemExit(128 + number)))
    try:
        with (args.output / 'heartbeat.jsonl').open('x') as stream:
            while time.time() <= deadline:
                if monitor.is_file():
                    data = monitor.read_bytes()
                    lines = data.splitlines()
                    if data and not data.endswith(b'\n'):
                        lines = lines[:-1]
                    if lines:
                        last = json.loads(lines[-1])
                        age = time.time() - stamp(last['at_utc'])
                        if time.time() > stamp(schedule['start_utc']) + 45 and not 0 <= age <= 45:
                            raise RuntimeError('Capture monitor heartbeat stale')
                        if last['collector_metrics']['status'] != 'success':
                            raise RuntimeError('Collector metrics query failed')
                elif time.time() > stamp(schedule['start_utc']) + 45:
                    raise RuntimeError('Capture monitor journal absent')
                row = {'at_utc': now(), 'capture_monitor_present': monitor.is_file(),
                       'collector_uid': args.collector_uid}
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                result['samples'] += 1
                terminal = args.capture_root / 'execution-result.json'
                if terminal.is_file():
                    capture = read(terminal)
                    if capture['status'] != 'VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT':
                        raise RuntimeError('Capture terminal failed: ' + capture['status'])
                    result['status'] = 'NOFAULT_OBSERVER_COMPLETE'
                    break
                time.sleep(15)
            else:
                raise RuntimeError('Observer exceeded capture and late-audit deadline')
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        result['completed_utc'] = now()
        with (args.output / 'terminal.json').open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps(result), flush=True)
    if result['status'] != 'NOFAULT_OBSERVER_COMPLETE':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
