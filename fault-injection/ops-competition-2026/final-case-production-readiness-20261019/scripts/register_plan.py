#!/usr/bin/env python3
"""Pin a new no-fault 1800-second run to the proven LOW-STABLE sources."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-readiness-20261019'
SAFETY = OPS / 'final-case-timer-safety-v1-20261019'
OLD = OPS / 'low-stable-v4-20261017'
REFERENCE = OLD / 'design/full-chain-low-stable-public-safe-contract.json'
DESIGN = ROOT / 'design/harness-draft.json'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('No-fault readiness plan already exists')
    reference = json.loads(REFERENCE.read_text())
    design = json.loads(DESIGN.read_text())
    if (reference['status'] != 'PREREGISTERED_FULL_CHAIN_LOW_STABLE_PUBLIC_SAFE_1800S' or
            reference['faults_allowed'] is not False or
            design['status'] != 'DRAFT_NOFAULT_READINESS_HARNESS' or
            design['duration_seconds'] != 1800 or
            design['real_injection_allowed'] is not False or
            design['run_id'] != 'SF-FINAL-NOFAULT-1800-20261019-01' or
            Path(design['output_root']).exists()):
        raise RuntimeError('Reference or new no-fault scope invalid')
    if any(sha(path) != digest for path, digest in reference['source_sha256'].items()):
        raise RuntimeError('Existing qualified no-fault source changed')
    plan = dict(reference)
    plan['status'] = 'PREREGISTERED_FINAL_CASE_NOFAULT_1800_MARKER_ONLY'
    plan['created_utc'] = datetime.now(timezone.utc).isoformat()
    plan['reference_plan_sha256'] = sha(REFERENCE)
    plan['readiness_design_sha256'] = sha(DESIGN)
    plan['runs'] = {'VAL6': {'duration_seconds': 1800,
                              'seed': design['workload_seed']}}
    plan['readiness_harness'] = {
        'run_id': design['run_id'],
        'real_fault_adapter_access': False,
        'marker_only': True,
        'planned_marker_offset_seconds': 600,
        'output_root': design['output_root']}
    mapped = {}
    for original in reference['source_sha256']:
        old = Path(original)
        if old.parent == OLD / 'validation-v6':
            current = ROOT / 'validation-v6' / old.name
        else:
            current = old
        if not current.is_file():
            raise RuntimeError('No-fault source missing: ' + str(current))
        mapped[str(current)] = sha(current)
    for directory in (ROOT / 'scripts', SAFETY / 'scripts',
                      OPS / 'sf013-adapter-v3-20261019/scripts'):
        for path in directory.glob('*.py'):
            mapped[str(path)] = sha(path)
    plan['source_sha256'] = dict(sorted(mapped.items()))
    if (len(plan['source_sha256']) < 35 or
            not set(plan['workload']['control_concurrency_per_stream']) ==
            {'image_asset', 'cart_view', 'home', 'recommendations'}):
        raise RuntimeError('No-fault source universe incomplete')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(plan, stream, indent=2)
        stream.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status': plan['status'], 'sha256': sha(args.output),
                      'source_count': len(plan['source_sha256']),
                      'faults_allowed': plan['faults_allowed']}))


if __name__ == '__main__':
    main()
