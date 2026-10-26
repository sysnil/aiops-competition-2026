"""Freeze source hashes before a case-specific 1,800-second storage qualification."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OPS = ROOT.parents[1]
CONTRACT = ROOT / 'case-budgets/sf008-att08.json'
SOURCES = (
    ROOT / 'storage_forecast_gate.py', ROOT / 'validate_heartbeat.py',
    ROOT / 'dynamic_storage_case.py', ROOT / 'qualify_case_budget.py',
    OPS / 'sf003-to-sf012-production-20261015/scripts/dynamic_storage.py',
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    value = json.loads(CONTRACT.read_text())
    if (value['status'] != 'PREREGISTERED_SF008_ATT08_STORAGE_BUDGET' or
            value['duration_seconds'] != 1800 or
            value['absolute_free_floor_bytes'] != 5_000_000_000):
        raise RuntimeError('Case budget differs from preregistered contract')
    result = {
        'status': 'PREREGISTERED_SF008_ATT08_STORAGE_NOFAULT_1800S',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'case_budget_sha256': sha(CONTRACT),
        'duration_seconds': 1800, 'sample_interval_seconds': 15,
        'minimum_samples': 120, 'faults_allowed': False,
        'formal_cases_allowed': False,
        'source_sha256': {str(path): sha(path) for path in SOURCES},
    }
    path = ROOT / 'case-budgets/sf008-att08-qualification-registration.json'
    with path.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'sha256': sha(path)}))


if __name__ == '__main__':
    main()
