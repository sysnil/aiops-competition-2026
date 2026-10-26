"""Freeze the prospective storage monitor's no-fault qualification contract."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OPS = ROOT.parents[1]
SOURCES = (
    ROOT / 'storage_forecast_gate.py',
    ROOT / 'qualify_live.py',
    OPS / 'sf003-to-sf012-production-20261015/scripts/dynamic_storage.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    contract = {
        'status': 'PREREGISTERED_STORAGE_V2_NOFAULT_1800S',
        'id': 'STORAGE-V2-NOFAULT-01',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'duration_seconds': 1800, 'sample_interval_seconds': 15,
        'minimum_samples': 120, 'faults_allowed': False,
        'formal_cases_allowed': False,
        'absolute_floor_bytes': 5_000_000_000,
        'emergency_margin_bytes': 2_000_000_000,
        'consecutive_forecast_breaches_to_stop': 3,
        'reserved_case_bytes': 7_350_324_113,
        'data_write_location': 'Mac local qualification log; copy to Master after verification',
        'source_sha256': {str(path): sha(path) for path in SOURCES},
    }
    path = ROOT / 'qualification-contract.json'
    with path.open('x') as stream:
        json.dump(contract, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': contract['status'], 'sha256': sha(path)}))


if __name__ == '__main__':
    main()
