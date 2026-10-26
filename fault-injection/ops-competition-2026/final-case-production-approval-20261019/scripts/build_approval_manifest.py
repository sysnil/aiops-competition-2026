#!/usr/bin/env python3
"""Hash DRAFT code/contracts/evidence and derive a fail-closed approval decision."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-approval-20261019'
W3 = ROOT / 'worker3-auth'
EVIDENCE = {
    'worker3_markers': ROOT / 'results/worker3-marker-assessment.json',
    'worker3_deployment': ROOT / 'results/worker3-deployment.json',
    'worker3_credential_check': W3 / 'results/worker3-credentials-verified.json',
    'worker3_rollback': W3 / 'results/worker3-rollback-dry-check.json',
    'restart_environment': ROOT / 'results/restart-environment-audit.json',
    'grant_races': ROOT / 'results/grant-race-analysis.json',
    'entrypoints': ROOT / 'results/five-case-entrypoint-audit.json',
    'legacy_lease': ROOT / 'results/legacy-lease-bypass-probe.json',
    'storage': ROOT / 'results/two-storage-allocations-final.json',
    'final_health': ROOT / 'results/five-host-final.json',
    'collector': ROOT / 'results/collector-final.json',
    'calico_bgp': ROOT / 'results/calico-bgp-final.json',
    'qdisc': ROOT / 'results/qdisc-final.json',
    'historical_public': ROOT / 'results/historical-public-final.json',
    'previous_v4_auth': OPS / 'final-case-fail-closed-v1-20261019/results/code-and-evidence-manifest.json',
}
CONTRACTS = {
    'preregistration_draft': OPS / 'final-cases-20261019/design/SF-013-to-SF-017-preregistration-draft.json',
    'root_archive_draft': OPS / 'final-case-timer-safety-v1-20261019/design/archive-allocation-draft.json',
    'restart_maintenance_plan': ROOT / 'design/restart-maintenance-window-plan.md',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Approval manifest already exists')
    data = {name: json.loads(path.read_text()) for name, path in EVIDENCE.items()}
    checks = {
        'worker3_marker_only_pass':
            data['worker3_markers']['status'] == 'WORKER3_MARKER_EXTENSION_PASS' and
            data['worker3_markers']['passed'] == 10,
        'worker3_restricted_ssh_pass':
            data['worker3_deployment']['status'] == 'WORKER3_AUTH_DEPLOYMENT_PASS' and
            data['worker3_credential_check']['status'] == 'WORKER3_CREDENTIALS_VERIFIED' and
            data['worker3_rollback']['status'] == 'WORKER3_SSH_ROLLBACK_DRY_CHECK_PASS',
        'five_host_healthy':
            data['final_health']['status'] == 'FIVE_HOST_READ_ONLY_HEALTH_PASS' and
            data['collector']['status'] == 'COLLECTOR_STORAGE_READ_ONLY_PASS' and
            data['calico_bgp']['status'] == 'CALICO_BGP_FOUR_NODES_THREE_OF_THREE_PASS' and
            data['qdisc']['status'] == 'FIVE_HOST_FAULT_QDISC_RESIDUE_PASS',
        'historical_public_unchanged':
            data['historical_public']['status'] == 'TEN_HISTORICAL_READY_PUBLIC_SHA_PASS',
        'credible_isolated_reboot_pass': False,
        'five_case_formal_entrypoints_bound': False,
        'grant_revocation_race_resolved': False,
        'five_case_storage_sensitivity_pass': False,
    }
    if (data['restart_environment']['status'] !=
            'NO_CREDIBLE_ISOLATED_SYSTEMD_REBOOT_ENV' or
            data['grant_races']['status'] != 'RESIDUAL_GRANT_RACES_CONFIRMED' or
            data['legacy_lease']['status'] != 'LEGACY_LOCAL_LEASE_BYPASS_CONFIRMED' or
            data['entrypoints']['status'] !=
            'FIVE_CASE_ENTRYPOINTS_AUDITED_PRODUCTION_BLOCKED' or
            data['storage']['status'] != 'TWO_UNFROZEN_ALLOCATIONS_COMPARED'):
        raise RuntimeError('Blocking evidence changed unexpectedly')
    source = sorted([*W3.joinpath('scripts').glob('*.py'),
                     *ROOT.joinpath('scripts').glob('*.py')])
    result = {
        'status': 'PRODUCTION_BLOCKED',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'checks': checks,
        'source_sha256': {str(path): sha(path) for path in source},
        'contract_sha256': {name: sha(path) for name, path in CONTRACTS.items()},
        'evidence_sha256': {name: sha(path) for name, path in EVIDENCE.items()},
        'real_faults_injected': 0,
        'new_formal_attempts_created': 0,
        'production_reboot_performed': False,
        'private_keys_read_or_exported': False,
        'committee_approval_granted': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'source_files': len(source),
                      'evidence_files': len(EVIDENCE),
                      'blocked_checks': [name for name, passed in checks.items()
                                         if not passed],
                      'manifest_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
