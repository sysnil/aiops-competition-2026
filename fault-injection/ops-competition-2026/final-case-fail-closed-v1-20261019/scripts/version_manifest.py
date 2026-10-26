#!/usr/bin/env python3
"""Version the new DRAFT auth code and immutable evidence after no-fault completion."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-fail-closed-v1-20261019'
ADAPTER = OPS / 'sf013-adapter-v4-20261019'
FORMAL = OPS / 'sf013-formal-v4-20261019'
DATA = Path('/data/stage5-v3/ops-competition-2026/final-case-fail-closed-v1-20261019')
SECRETS = OPS / 'final-case-auth-secrets-20261019'
EVIDENCE = {
    'marker_matrix': ROOT / 'results/marker-matrix-assessment-v2.json',
    'formal_wiring': ROOT / 'results/formal-wiring-audit.json',
    'deployed_hashes': ROOT / 'results/deployed-hashes.json',
    'live_auth_nofault': ROOT / 'results/no-fault-1800-live-auth-01/joint-terminal.json',
    'live_auth_assessment': DATA / 'validation-runs-v7/VAL7/assessment.json',
    'collector_final': ROOT / 'results/collector-final.json',
    'five_host_final': ROOT / 'results/five-host-final.json',
    'five_host_monitor': ROOT / 'results/five-host-no-fault-v4-terminal.json',
    'calico_bgp': ROOT / 'results/calico-bgp-final.json',
    'qdisc_final': ROOT / 'results/qdisc-final.json',
    'historical_public': ROOT / 'results/historical-public-final.json',
    'rollback_dry_check': ROOT / 'results/rollback-dry-check.json',
    'master_provision': ROOT / 'results/master-credential-provision.json',
    'watchdog_selftest': ROOT / 'results/watchdog-selftest-v4.json',
}


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
        raise RuntimeError('Immutable manifest exists')
    source = sorted(path for folder in (ROOT / 'scripts',
                                          ROOT / 'validation-v7',
                                          ADAPTER / 'scripts',
                                          FORMAL / 'scripts')
                    for path in folder.glob('*.py'))
    evidence = {name: json.loads(path.read_text())
                for name, path in EVIDENCE.items()}
    if (evidence['marker_matrix']['status'] != 'FAIL_CLOSED_MARKER_MATRIX_PASS' or
            evidence['marker_matrix']['passed'] != 19 or
            evidence['formal_wiring']['status'] !=
                'FORMAL_MARKER_AUTH_WIRING_STATIC_PASS' or
            evidence['deployed_hashes']['status'] !=
                'DEPLOYED_LIVE_AUTH_HASHES_PASS' or
            evidence['live_auth_nofault']['status'] != 'NOFAULT_1800_CHAIN_PASS' or
            evidence['live_auth_assessment']['status'] != 'NO_FAULT_VALIDATION_PASS' or
            evidence['five_host_final']['status'] !=
                'FIVE_HOST_READ_ONLY_HEALTH_PASS' or
            evidence['five_host_monitor']['status'] !=
                'FIVE_HOST_NOFAULT_MONITOR_COMPLETE' or
            evidence['calico_bgp']['status'] !=
                'CALICO_BGP_FOUR_NODES_THREE_OF_THREE_PASS' or
            evidence['historical_public']['status'] !=
                'TEN_HISTORICAL_READY_PUBLIC_SHA_PASS' or
            evidence['watchdog_selftest']['status'] !=
                'V4_HARMLESS_WATCHDOG_SELFTEST_PASS' or
            any(not path.is_file() for path in EVIDENCE.values())):
        raise RuntimeError('Required evidence missing or failed')
    plan = ROOT / 'design/live-auth-nofault-1800.json'
    plan_data = json.loads(plan.read_text())
    if any(sha(path) != digest for path, digest in
           plan_data['source_sha256'].items()):
        raise RuntimeError('No-fault source pin changed')
    result = {
        'status': 'LIVE_AUTH_DRAFT_VERSIONED_NOT_PRODUCTION_PERMIT',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {str(path): sha(path) for path in source},
        'evidence_sha256': {name: sha(path) for name, path in EVIDENCE.items()},
        'no_fault_plan_sha256': sha(plan),
        'master_signing_public_sha256': sha(SECRETS / 'master-signing-public.pem'),
        'master_private_key_recorded': False,
        'worker_private_key_recorded': False,
        'real_faults_injected': 0,
        'new_formal_cases_created': 0,
        'isolated_systemd_reboot_test_passed': False,
        'production_authorized': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'source_files': len(source),
                      'evidence_files': len(EVIDENCE),
                      'manifest_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
