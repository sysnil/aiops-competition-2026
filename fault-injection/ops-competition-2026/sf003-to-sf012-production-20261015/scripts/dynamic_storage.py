#!/usr/bin/env python3
"""Read-only five-gigabyte capacity gate for a prospective formal runner."""

import argparse
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path


FLOOR = 5_000_000_000
HOSTS = {
    'master': ('192.168.53.52', '/tmp/sfgen-master.sock', '/data/stage5-v3'),
    'worker1': ('192.168.53.53', '/tmp/sfgen-w1.sock', '/data'),
    'worker2': ('192.168.53.54', '/tmp/sfgen-w2.sock', '/data'),
    'worker3': ('192.168.53.55', '/tmp/sfgen-w3.sock', '/data'),
    'vm1': ('192.168.53.50', '/tmp/sfgen-router.sock', None),
}
MEASURED_CASE_BYTES = 5_880_259_290
SENSITIVITY_CASE_BYTES = (MEASURED_CASE_BYTES * 5 + 3) // 4


def remote(host):
    address, socket, data = HOSTS[host]
    code = '''import json,os,shutil
root=shutil.disk_usage('/')
result={'root_free_bytes':root.free}
if %r is not None:
    assert os.path.ismount(%r) and os.stat('/').st_dev != os.stat(%r).st_dev
    result.update({'data_free_bytes':shutil.disk_usage(%r).free,'data_mount':%r})
print(json.dumps(result))
''' % (data, data, data, data, data)
    proc = subprocess.run(
        ['ssh', '-S', socket, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
         'root@' + address, shlex.join(['python3', '-c', code])],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return json.loads(proc.stdout)


def decide(snapshot, forecast, fault_active=False, cleanup_armed=False):
    if fault_active and not cleanup_armed:
        return {'status': 'ABORT_AND_CLEANUP_REQUIRED',
                'reason': 'ACTIVE_FAULT_WITHOUT_INDEPENDENT_CLEANUP',
                'fault_permission': False}
    for host in HOSTS:
        if host not in snapshot or host not in forecast:
            return {'status': 'ABORT_AND_CLEANUP_REQUIRED' if fault_active else 'STOP_NEW_WORK',
                    'reason': 'MISSING_HOST_OR_FORECAST_' + host,
                    'fault_permission': False}
        disks = ('root_free_bytes', 'data_free_bytes') if HOSTS[host][2] else ('root_free_bytes',)
        for disk in disks:
            value = snapshot[host].get(disk)
            expected = forecast[host].get(disk)
            if not isinstance(value, int) or not isinstance(expected, int) or expected < 0:
                return {'status': 'ABORT_AND_CLEANUP_REQUIRED' if fault_active else 'STOP_NEW_WORK',
                        'reason': 'INVALID_SNAPSHOT_OR_FORECAST_' + host + '_' + disk,
                        'fault_permission': False}
            if value < FLOOR or value - expected < FLOOR:
                return {'status': 'ABORT_AND_CLEANUP_REQUIRED' if fault_active else 'STOP_NEW_WORK',
                        'reason': 'PREDICTED_FIVE_GB_FLOOR_' + host + '_' + disk,
                        'remaining_after_forecast_bytes': value - expected,
                        'fault_permission': False}
    return {'status': 'DYNAMIC_STORAGE_PASS',
            'floor_bytes': FLOOR, 'fault_permission': False,
            'meaning': 'Storage only; mechanism, time, safety and data gates still required'}


def forecast_remaining(previous, current, elapsed_seconds, remaining_seconds,
                       committed_bytes=None, rate_multiplier=1.5):
    """Forecast each mount from observed consumption plus future committed writes."""
    if elapsed_seconds <= 0 or remaining_seconds < 0 or rate_multiplier < 1:
        raise ValueError('Invalid live forecast window')
    committed_bytes = committed_bytes or {}
    forecast = {}
    for host in HOSTS:
        forecast[host] = {}
        disks = ('root_free_bytes', 'data_free_bytes') if HOSTS[host][2] else ('root_free_bytes',)
        for disk in disks:
            old, new = previous[host][disk], current[host][disk]
            if not isinstance(old, int) or not isinstance(new, int):
                raise ValueError('Incomplete disk samples')
            rate_spend = max(0, old - new) / elapsed_seconds
            predicted = int(rate_spend * remaining_seconds * rate_multiplier + 0.999999)
            commitment = committed_bytes.get(host, {}).get(disk, 0)
            if not isinstance(commitment, int) or commitment < 0:
                raise ValueError('Invalid committed bytes')
            forecast[host][disk] = max(predicted, commitment)
    return forecast


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--remaining-case-bytes', type=int, default=SENSITIVITY_CASE_BYTES)
    args = p.parse_args()
    if args.output.exists() or args.remaining_case_bytes < 0:
        raise RuntimeError('New output and nonnegative forecast required')
    snapshot = {host: remote(host) for host in HOSTS}
    # One prospective new case. During collection, the runner must replace
    # this static starting forecast with observed remaining capture/archive rates.
    forecast = {host: {'root_free_bytes': 64 * 1024**2, **({
        'data_free_bytes': args.remaining_case_bytes if host != 'worker1' else 0,
    } if HOSTS[host][2] else {})} for host in HOSTS}
    result = {
        'status': 'READ_ONLY_FIVE_GB_SNAPSHOT',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'snapshot': snapshot, 'forecast': forecast,
        'one_case': decide(snapshot, forecast),
        'ten_cases_worker2_25pct_final_free_bytes':
            snapshot['worker2']['data_free_bytes'] - 10 * SENSITIVITY_CASE_BYTES,
        'ten_cases_complete_without_expansion':
            snapshot['worker2']['data_free_bytes'] - 10 * SENSITIVITY_CASE_BYTES >= FLOOR,
        'old_peak_reservation_used_as_hard_gate': False,
        'faults_injected': 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({'status': result['status'],
                      'one_case_storage': result['one_case']['status'],
                      'worker2_ten_case_25pct_final_GB':
                          round(result['ten_cases_worker2_25pct_final_free_bytes'] / 1e9, 3)}))


if __name__ == '__main__':
    main()
