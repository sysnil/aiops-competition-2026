"""Exercise the exact five-host read path without a fault or formal case."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time


OPS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OPS / 'sf003-to-sf012-production-20261015/scripts'))
from dynamic_storage import HOSTS, decide, remote


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--terminal', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.terminal.exists():
        raise RuntimeError('Heartbeat qualification already exists')
    contract = json.loads(args.contract.read_text())
    if (contract['status'] != 'PREREGISTERED_READ_ONLY_HEARTBEAT_QUALIFICATION' or
            contract['duration_seconds'] != 1800 or contract['interval_seconds'] != 15 or
            contract['allowed_missing_or_timeout_cycles'] != 0 or
            contract['faults_allowed'] is not False):
        raise RuntimeError('Unregistered heartbeat qualification contract')
    forecast = {host: {'root_free_bytes': 64 * 1024**2,
                       **({'data_free_bytes': contract['forecast_case_bytes']
                           if host in ('master', 'worker2', 'worker3') else 0}
                          if HOSTS[host][2] else {})} for host in HOSTS}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    result = {'status': 'FIVE_HOST_HEARTBEAT_QUALIFICATION_FAILED',
              'started_utc': now(), 'faults_injected': 0,
              'contract_sha256': sha(args.contract), 'source_sha256': sha(Path(__file__)),
              'planned_samples': 121, 'completed_samples': 0}
    try:
        with args.output.open('x') as stream:
            for index in range(121):
                planned = began + index * 15
                time.sleep(max(0, planned - time.monotonic()))
                lag = time.monotonic() - planned
                if lag > contract['maximum_schedule_lag_seconds']:
                    raise RuntimeError('Heartbeat schedule lag exceeded contract')
                measured = {}
                latencies = {}
                started = time.monotonic()
                for host, (_, socket, _) in HOSTS.items():
                    if not Path(socket).is_socket():
                        raise RuntimeError('Missing SSH control socket: ' + host)
                    begin_host = time.monotonic()
                    measured[host] = remote(host)
                    latencies[host] = time.monotonic() - begin_host
                cycle = time.monotonic() - started
                decision = decide(measured, forecast)
                row = {'at_utc': now(), 'sample_index': index,
                       'schedule_lag_seconds': lag, 'cycle_seconds': cycle,
                       'host_query_seconds': latencies, 'storage': decision['status'],
                       'snapshot': measured}
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                result['completed_samples'] += 1
                if (cycle > contract['maximum_cycle_seconds'] or
                        decision['status'] != 'DYNAMIC_STORAGE_PASS'):
                    raise RuntimeError('Heartbeat cycle or five-GB forecast failed')
        result['status'] = 'FIVE_HOST_HEARTBEAT_1800S_PASS'
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        result['completed_utc'] = now()
        result['journal_sha256'] = sha(args.output)
        with args.terminal.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps(result))
    if result['status'] != 'FIVE_HOST_HEARTBEAT_1800S_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
