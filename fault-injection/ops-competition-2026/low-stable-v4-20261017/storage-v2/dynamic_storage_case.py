"""Read-only five-host capacity gate using the case's actual archive placement."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
OPS = ROOT.parents[1]
sys.path.insert(0, str(OPS / 'sf003-to-sf012-production-20261015/scripts'))
from dynamic_storage import HOSTS, decide, remote


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_path(source):
    case, attempt = source.split('-ATTEMPT-', 1)
    return OPS / 'low-stable-v4-20261017/results' / (
        case.lower().replace('-', '') + '-att' + attempt + '-five-host-live.jsonl')


def validate_contract(contract):
    if (contract['status'] != 'PREREGISTERED_SF008_ATT08_STORAGE_BUDGET' or
            contract['run_id'] != 'SF-008-ATTEMPT-08' or
            contract['duration_seconds'] != 1800 or
            contract['absolute_free_floor_bytes'] != 5_000_000_000 or
            contract['root_reservation_bytes_per_host'] != 64 * 1024**2 or
            contract['archive_target'] != 'worker2' or
            set(contract['data_reservation_bytes']) !=
                {'master', 'worker1', 'worker2', 'worker3'} or
            contract['data_reservation_bytes'] != {
                'master': 7_350_324_113, 'worker1': 2_000_000_000,
                'worker2': 7_350_324_113, 'worker3': 2_000_000_000}):
        raise RuntimeError('Case-specific storage commitment changed')
    for evidence in contract['worker3_observed_windows']:
        path = evidence_path(evidence['source'])
        rows = [json.loads(line) for line in path.read_text().splitlines()
                if json.loads(line).get('snapshot')]
        if (sha(path) != evidence['monitor_sha256'] or
                len(rows) != evidence['samples'] or
                rows[0]['snapshot']['worker3']['data_free_bytes'] -
                rows[-1]['snapshot']['worker3']['data_free_bytes'] !=
                evidence['net_spend_bytes']):
            raise RuntimeError('Worker3 write-rate evidence changed')


def forecast(contract):
    amount = contract['data_reservation_bytes']
    return {host: {'root_free_bytes': contract['root_reservation_bytes_per_host'],
                   **({'data_free_bytes': amount[host]}
                      if HOSTS[host][2] else {})} for host in HOSTS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Snapshot already exists')
    contract = json.loads(args.contract.read_text())
    validate_contract(contract)
    snapshot = {host: remote(host) for host in HOSTS}
    plan = forecast(contract)
    decision = decide(snapshot, plan)
    result = {
        'status': 'READ_ONLY_FIVE_GB_SNAPSHOT',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'snapshot': snapshot, 'forecast': plan, 'one_case': decision,
        'commitment_contract_sha256': sha(args.contract),
        'preflight_source_sha256': sha(Path(__file__)),
        'archive_target': contract['archive_target'],
        'old_all_worker_peak_reservation_used': False,
        'faults_injected': 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'one_case_storage': decision['status'],
                      'archive_target': result['archive_target'],
                      'worker3_free_after_commitment':
                          snapshot['worker3']['data_free_bytes'] -
                          plan['worker3']['data_free_bytes']}))
    if decision['status'] != 'DYNAMIC_STORAGE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
