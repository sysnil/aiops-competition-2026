"""Read-only 1,800-second five-host qualification of the storage forecast gate."""

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parent
OPS = ROOT.parents[1]
sys.path.insert(0, str(OPS / 'sf003-to-sf012-production-20261015/scripts'))
from dynamic_storage import HOSTS, forecast_remaining, remote
from storage_forecast_gate import StorageForecastGate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def commitments(amount):
    return {host: {'root_free_bytes': 64 * 1024**2, **({
        'data_free_bytes': amount if host in ('master', 'worker2', 'worker3') else 0}
        if HOSTS[host][2] else {})} for host in HOSTS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract_path = ROOT / 'qualification-contract.json'
    contract = json.loads(contract_path.read_text())
    if (args.output.exists() or args.output.parent.exists() or
            contract['id'] != 'STORAGE-V2-NOFAULT-01' or
            contract['status'] != 'PREREGISTERED_STORAGE_V2_NOFAULT_1800S' or
            contract['faults_allowed'] is not False or
            any(sha(Path(path)) != value for path, value in
                contract['source_sha256'].items())):
        raise RuntimeError('New output and unchanged preregistered source required')
    args.output.parent.mkdir(parents=True, mode=0o700)
    terminal = args.output.parent / 'terminal.json'
    gate = StorageForecastGate()
    history = deque(maxlen=12)
    started = time.monotonic()
    duration = contract['duration_seconds']
    obligations = commitments(contract['reserved_case_bytes'])
    samples = 0
    pending = 0
    status = 'STORAGE_V2_NOFAULT_FAILED'
    error = None
    try:
        with args.output.open('x') as log:
            for index in range(duration // contract['sample_interval_seconds'] + 1):
                tick = started + index * contract['sample_interval_seconds']
                time.sleep(max(0, tick - time.monotonic()))
                if time.monotonic() - tick > 5:
                    raise RuntimeError('Storage qualification schedule missed')
                at = time.monotonic()
                snapshot = {host: remote(host) for host in HOSTS}
                mature = [row for row in history if at - row[0] >= 60]
                reference = mature[-1] if mature else None
                forecast = obligations if reference is None else forecast_remaining(
                    reference[1], snapshot, at - reference[0],
                    max(0, duration - (at - started)), obligations)
                decision = gate.evaluate(snapshot, forecast)
                row = {'at_utc': now(), 'index': index, 'snapshot': snapshot,
                       'forecast': forecast, 'decision': decision}
                log.write(json.dumps(row) + '\n')
                log.flush()
                samples += 1
                pending += bool(decision['pending_forecast_breaches'])
                history.append((at, snapshot))
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    raise RuntimeError('Storage qualification fail-closed: ' +
                                       decision['reason'])
        if samples < contract['minimum_samples']:
            raise RuntimeError('Insufficient five-host samples')
        status = 'STORAGE_V2_NOFAULT_1800_PASS'
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    result = {'status': status, 'id': contract['id'],
              'completed_utc': now(), 'duration_seconds': duration,
              'samples': samples, 'pending_breach_samples': pending,
              'faults_injected': 0, 'formal_cases_created': 0,
              'contract_sha256': sha(contract_path),
              'monitor_sha256': sha(args.output), 'error': error}
    with terminal.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result), flush=True)
    if status != 'STORAGE_V2_NOFAULT_1800_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
