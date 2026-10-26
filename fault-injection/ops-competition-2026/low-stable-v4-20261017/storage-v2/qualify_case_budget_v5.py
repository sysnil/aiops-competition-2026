"""Read-only 1800-second qualification of the Worker1 archive budget."""

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
from dynamic_storage_case_v5 import forecast, validate_contract
import dynamic_storage_case_v5
from storage_forecast_gate import StorageForecastGate

BUDGET = ROOT / 'case-budgets/sf007-att05.json'
REGISTRATION = ROOT / 'case-budgets/sf007-att05-qualification-registration.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(BUDGET.read_text())
    registration = json.loads(REGISTRATION.read_text())
    validate_contract(contract)
    if (args.output.exists() or args.output.parent.exists() or
            registration['status'] != 'PREREGISTERED_SF007_ATT05_BUDGET_NOFAULT_1800S' or
            registration['case_budget_sha256'] != sha(BUDGET) or
            registration['case_budget_reader_sha256'] != sha(Path(dynamic_storage_case_v5.__file__)) or
            registration['qualifier_sha256'] != sha(Path(__file__)) or
            registration['run_id'] != 'SF-007-ATTEMPT-05' or
            registration['archive_target'] != 'worker1' or
            registration['absolute_free_floor_bytes'] != 5_000_000_000 or
            registration['faults_allowed'] is not False or
            registration['duration_seconds'] != 1800 or
            registration['sample_interval_seconds'] != 15 or
            registration['minimum_samples'] != 120):
        raise RuntimeError('Changed or unregistered no-fault budget qualification')
    args.output.parent.mkdir(parents=True, mode=0o700)
    gate = StorageForecastGate()
    history = deque(maxlen=12)
    obligations = forecast(contract)
    started = time.monotonic()
    samples = pending = 0
    status, error = 'SF007_ATT05_BUDGET_NOFAULT_FAILED', None
    try:
        with args.output.open('x') as log:
            for index in range(121):
                tick = started + index * 15
                time.sleep(max(0, tick - time.monotonic()))
                if time.monotonic() - tick > 5:
                    raise RuntimeError('Scheduled capacity sample missed')
                at = time.monotonic()
                snapshot = {host: remote(host) for host in HOSTS}
                mature = [item for item in history if at - item[0] >= 60]
                reference = mature[-1] if mature else None
                predicted = obligations if reference is None else forecast_remaining(
                    reference[1], snapshot, at - reference[0],
                    max(0, 1800 - (at - started)), obligations)
                decision = gate.evaluate(snapshot, predicted)
                log.write(json.dumps({'at_utc': now(), 'index': index,
                                      'snapshot': snapshot, 'forecast': predicted,
                                      'decision': decision}) + '\n')
                log.flush()
                history.append((at, snapshot))
                samples += 1
                pending += bool(decision['pending_forecast_breaches'])
                if decision['status'] != 'DYNAMIC_STORAGE_PASS':
                    raise RuntimeError('Five-host budget stopped: ' + decision['reason'])
        if samples < 120:
            raise RuntimeError('Insufficient full-window capacity samples')
        status = 'SF007_ATT05_BUDGET_NOFAULT_1800_PASS'
    except BaseException as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    result = {'status': status, 'completed_utc': now(),
              'duration_seconds': 1800, 'samples': samples,
              'pending_breach_samples': pending, 'faults_injected': 0,
              'formal_cases_created': 0, 'case_budget_sha256': sha(BUDGET),
              'qualification_registration_sha256': sha(REGISTRATION),
              'monitor_sha256': sha(args.output), 'error': error}
    terminal = args.output.parent / 'terminal.json'
    with terminal.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result), flush=True)
    if status != 'SF007_ATT05_BUDGET_NOFAULT_1800_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
