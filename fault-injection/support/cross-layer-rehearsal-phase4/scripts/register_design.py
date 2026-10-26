#!/usr/bin/env python3
"""Bind pre-fault Phase 4 rules and reused source versions to hashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path('/root/cross-layer-rehearsal-phase4')
FILES = [ROOT / 'design/plan.yaml', ROOT / 'design/fault-adapters.yaml',
         ROOT / 'observations/no-fault-readiness.json',
         *sorted((ROOT / 'scripts').glob('*.py')),
         Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
         Path('/root/cross-layer-observability-control-rehearsal/scripts/fixed_control_load.py'),
         ROOT / 'design/workload-state-machine.yaml']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'design/registered-rehearsal-manifest.json')
    parser.add_argument('--revision-reason', default='initial_pre_fault_registration')
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise ValueError('Pre-fault registration already exists')
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
    value = {'phase': 'cross-layer-rehearsal-phase4',
             'registered_at_utc': datetime.now(timezone.utc).isoformat(),
             'scope': 'independent_rehearsal_not_stage5_v3_formal',
             'revision_reason': args.revision_reason,
             'experiments': ['P4-W3W2-S1', 'P4-W3W2-S2',
                             'P4-PAYMENT-S1', 'P4-PAYMENT-S2'],
             'faults_injected_before_registration': 0,
             'no_fault_readiness_sha256': hashes[str(ROOT / 'observations/no-fault-readiness.json')],
             'sha256': hashes}
    output.write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps({'status': 'PHASE4_REHEARSAL_RULES_REGISTERED',
                      'files': len(hashes),
                      'manifest_sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
