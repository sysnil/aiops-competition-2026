"""Freeze only the prospective SF-011 ATTEMPT-03 execution inputs."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time


OPS = Path('/root/ops-competition-2026')
ADAPTER = OPS / 'sf011-http-adapter-v3-20261018'
FORMAL = OPS / 'sf011-http-formal-v3-20261018'
AUTHORITY = OPS / 'low-stable-v4-20261017/design/sf011-attempt03-retry-draft.json'
QUALIFICATION = OPS / 'low-stable-v4-20261017/storage-v2/results/SF007-ATT06-BUDGET-NOFAULT-01/terminal.json'
VALIDATION = Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017/validation-runs-v6/VAL6/assessment.json')
OUTPUT = OPS / 'low-stable-v4-20261017/design/sf011-attempt03-freeze-manifest.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise RuntimeError('Prospective input freeze already exists')
    authority = json.loads(AUTHORITY.read_text())
    qualification = json.loads(QUALIFICATION.read_text())
    validation = json.loads(VALIDATION.read_text())
    age = time.time() - datetime.fromisoformat(qualification['completed_utc']).timestamp()
    if (authority['run_id'] != 'SF-011-ATTEMPT-03' or
            authority['minimum_filesystem_free_bytes'] != 5_000_000_000 or
            qualification['status'] != 'SF007_ATT06_BUDGET_NOFAULT_1800_PASS' or
            qualification['samples'] != 121 or qualification['faults_injected'] != 0 or
            qualification['formal_cases_created'] != 0 or not 0 <= age <= 3600 or
            validation['status'] != 'NO_FAULT_VALIDATION_PASS' or
            validation['faults_injected'] != 0):
        raise RuntimeError('Fresh no-fault qualification and authority required')
    paths = sorted(ADAPTER.joinpath('scripts').glob('*.py'))
    paths += sorted(FORMAL.joinpath('scripts').glob('*.py'))
    paths += sorted(FORMAL.joinpath('design').glob('*'))
    paths.append(AUTHORITY)
    sources = {str(path): sha(path) for path in paths if path.is_file()}
    result = {'status': 'SF011_ATTEMPT03_INPUTS_FROZEN',
              'run_id': 'SF-011-ATTEMPT-03',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'source_sha256': sources,
              'public_safe_no_fault_assessment_sha256': sha(VALIDATION),
              'heartbeat_qualification_sha256': sha(QUALIFICATION),
              'faults_injected': 0, 'formal_case_created': False,
              'scope': 'new ATTEMPT-03 inputs only; prior SF-011 attempts unchanged'}
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'], 'sha256': sha(OUTPUT),
                      'code_and_contract_files': len(sources)}))


if __name__ == '__main__':
    main()
