#!/usr/bin/env python3
"""Fresh exact-PID cleanup selftest using the unchanged competition adapter."""

import hashlib
import importlib.util
import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


ADAPTER = Path('/root/ops-competition-2026/scripts/competition_adapter.py')
ADAPTER_SHA = '791481b3e12ce6f64bb77c75acaff7df8000d800081589194ccde5d5ad3f2a92'
OUTPUT = Path('/root/ops-competition-2026/qualification/SF006-WATCHDOG-SELFTEST-W2-ATTEMPT01.json')
TOKEN = 'sf006-watchdog-selftest-w2-attempt01-20261017'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    if socket.gethostname() != 'k8s-worker2' or sha(ADAPTER) != ADAPTER_SHA or OUTPUT.exists():
        raise RuntimeError('Wrong host/adapter or previous selftest exists')
    spec = importlib.util.spec_from_file_location('qualified_adapter', ADAPTER)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    state_path = adapter.RUNTIME / (TOKEN + '.json')
    if state_path.exists():
        raise RuntimeError('Previous selftest state exists')
    child = subprocess.Popen(['/usr/bin/sleep', '30'])
    target = adapter.proc(child.pid)
    state = {'id': TOKEN, 'host': socket.gethostname(), 'layer': 'SELFTEST',
             'status': 'ARMED', 'stopped_targets': [target], 'children': []}
    unit = 'ops-' + TOKEN
    result = {'status': 'SF006_WATCHDOG_SELFTEST_FAILED', 'at_utc': now(),
              'business_faults_injected': 0, 'adapter_sha256': ADAPTER_SHA,
              'source_sha256': sha(__file__), 'target_pid': child.pid}
    try:
        adapter.save(state_path, state)
        adapter.arm(state_path, 3, unit)
        os.kill(child.pid, signal.SIGSTOP)
        time.sleep(5)
        final = adapter.read(state_path)
        live = adapter.proc(child.pid)
        if final['status'] != 'CLEANED' or not adapter.same(target) or (
                live is None or live['process_state'] in ('T', 't')):
            raise RuntimeError('Independent timer did not restore exact sacrificial PID')
        result.update(status='COMPETITION_WATCHDOG_SELFTEST_PASS',
                      cleanup_result=final['cleanup_result'],
                      timer_unit=unit, timer_accuracy='100ms')
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        if adapter.same(target):
            os.kill(child.pid, signal.SIGCONT)
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)
        try:
            adapter.stop_timer(unit)
        except Exception as exc:
            result['status'] = 'SF006_WATCHDOG_CLEANUP_FAILED'
            result['timer_cleanup_error'] = str(exc)
        result['completed_utc'] = now()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        OUTPUT.chmod(0o600)
        print(json.dumps({'status': result['status'], 'path': str(OUTPUT)}))
    if result['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
