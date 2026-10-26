"""Read-only five-host no-fault check of a frozen per-host case budget."""

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
from dynamic_storage_case import forecast, validate_contract
from storage_forecast_gate import StorageForecastGate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract_path = ROOT / 'case-budgets/sf008-att08.json'
    registration_path = ROOT / 'case-budgets/sf008-att08-qualification-registration.json'
    contract, registration = (json.loads(path.read_text())
                              for path in (contract_path, registration_path))
    validate_contract(contract)
    if (args.output.exists() or args.output.parent.exists() or
            registration['status'] != 'PREREGISTERED_SF008_ATT08_STORAGE_NOFAULT_1800S' or
            registration['case_budget_sha256'] != sha(contract_path) or
            registration['faults_allowed'] is not False or
            any(sha(Path(path)) != expected for path, expected in
                registration['source_sha256'].items())):
        raise RuntimeError('New output and unchanged preregistered source required')
    args.output.parent.mkdir(parents=True, mode=0o700)
    terminal = args.output.parent / 'terminal.json'
    gate = StorageForecastGate()
    history = deque(maxlen=12)
    started = time.monotonic()
    duration = registration['duration_seconds']
    obligations = forecast(contract)
    samples = pending = 0
    status, error = 'SF008_ATT08_BUDGET_QUALIFICATION_FAILED', None
    try:
        with args.output.open('x') as log:
            for index in range(duration // registration['sample_interval_seconds'] + 1):
                tick = started + index * registration['sample_interval_seconds']
                time.sleep(max(0, tick - time.monotonic()))
                if time.monotonic() - tick > 5:
                    raise RuntimeError('Case budget qualification schedule missed')
                at = time.monotonic()
                snapshot = {host: remote(host) for host in HOSTS}
                mature = [row for row in history if at - row[0] >= 60]
                reference = mature[-1] if mature else None
                predicted = obligations if reference is None else forecast_remaining(
                    reference[1], snapshot, at - reference[0],
                    max(0, duration - (at - started)), obligations)
                decision = gate.evaluate(snapshot, predicted)
                row = {'at_utc': now(), 'index': index, 'snapshot': snapshot,
                       'forecast': predicted, 'decision': decision}
                log.write(json.dumps(row) + '\n')
                log.flush()
                samples += 1
                pending += bool(decision['pending_forecast_breaches'])
                history.append((at, snapshot))
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    raise RuntimeError('Case budget fail-closed: ' + decision['reason'])
        if samples < registration['minimum_samples']:
            raise RuntimeError('Insufficient five-host samples')
        status = 'SF008_ATT08_BUDGET_NOFAULT_1800_PASS'
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    result = {
        'status': status, 'completed_utc': now(), 'duration_seconds': duration,
        'samples': samples, 'pending_breach_samples': pending,
        'faults_injected': 0, 'formal_cases_created': 0,
        'case_budget_sha256': sha(contract_path),
        'qualification_registration_sha256': sha(registration_path),
        'monitor_sha256': sha(args.output), 'error': error,
    }
    with terminal.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result), flush=True)
    if status != 'SF008_ATT08_BUDGET_NOFAULT_1800_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
