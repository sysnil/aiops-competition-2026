#!/usr/bin/env python3
"""Pin the reviewed Timer safety code and immutable test evidence."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-timer-safety-v1-20261019'
ADAPTER = OPS / 'sf013-adapter-v3-20261019'
FORMAL = OPS / 'sf013-formal-v3-20261019'
EVIDENCE = {
    'marker_suite_final': ROOT / 'results/marker-suite-20261019c.json',
    'two_attempt_isolation': ROOT / 'results/marker-isolation-20261019iso.json',
    'worker_watchdog': ROOT / 'results/watchdog-selftest-worker2.json',
    'historical_public': ROOT / 'results/ten-historical-ready-public-audit.json',
    'five_host_snapshot': ROOT / 'results/final-host-snapshot-v3.json',
    'five_host_residue': ROOT / 'results/final-host-residue-v3.json',
    'competition_units': ROOT / 'results/final-competition-units-v3.json',
    'collector': ROOT / 'results/collector-final-v3.json',
    'storage_projection': ROOT / 'results/sequential-storage-projection-v3.json',
    'archive_path_fixture': ROOT / 'results/root-archive-path-qualification.json',
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
        raise RuntimeError('Safety version manifest already exists')
    source = []
    for directory in (ADAPTER / 'scripts', ADAPTER / 'tests',
                      FORMAL / 'scripts', FORMAL / 'tests', FORMAL / 'design',
                      ROOT / 'scripts', ROOT / 'design'):
        source.extend(path for path in directory.iterdir()
                      if path.is_file() and path.suffix in ('.py', '.json', '.yaml'))
    if not source or any(not path.is_file() for path in EVIDENCE.values()):
        raise RuntimeError('Safety source or evidence missing')
    evidence = {name: json.loads(path.read_text()) for name, path in EVIDENCE.items()}
    if (evidence['marker_suite_final']['status'] != 'HARMLESS_MARKER_SUITE_PASS' or
            len(evidence['marker_suite_final']['results']) != 10 or
            evidence['marker_suite_final']['source_sha256'][str(
                ADAPTER / 'scripts/timer_lifecycle.py')] != sha(
                ADAPTER / 'scripts/timer_lifecycle.py') or
            evidence['two_attempt_isolation']['status'] !=
                'TWO_ATTEMPT_SYSTEMD_TIMER_ISOLATION_PASS' or
            evidence['two_attempt_isolation']['contract_sha256'] != sha(
                ROOT / 'design/marker-isolation-contract.json') or
            evidence['worker_watchdog']['status'] != 'COMPETITION_WATCHDOG_SELFTEST_PASS' or
            evidence['historical_public']['status'] != 'TEN_HISTORICAL_READY_PUBLIC_SHA_PASS' or
            evidence['five_host_snapshot']['status'] != 'READ_ONLY_PREFLIGHT_PASS' or
            evidence['five_host_residue']['status'] != 'HOST_RESIDUE_PASS' or
            evidence['competition_units']['status'] != 'COMPETITION_UNIT_RESIDUE_PASS' or
            evidence['collector']['status'] != 'COLLECTOR_STORAGE_READ_ONLY_PASS' or
            evidence['storage_projection']['status'] !=
                'SEQUENTIAL_STORAGE_PROJECTION_CONDITIONAL' or
            evidence['archive_path_fixture']['status'] !=
                'SMALL_FIXTURE_ROOT_ARCHIVE_TRANSFER_AND_READBACK_PASS' or
            (ADAPTER / 'plans/SF-013-ATTEMPT-03').exists()):
        raise RuntimeError('Safety evidence invalid or formal Attempt was created')
    result = {
        'status': 'TIMER_SAFETY_FIX_VERSIONED_MARKER_PASS_NOT_PRODUCTION_PERMIT',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'source_sha256': {str(path): sha(path) for path in sorted(source)},
        'evidence_sha256': {name: sha(path) for name, path in EVIDENCE.items()},
        'worker2_code_sha256_verified': {
            str(ADAPTER / 'scripts/timer_lifecycle.py'):
                '0c4b76a8c7ec1c361d7943d75a886c2721c296dcdb29a9bc4e5d82618c7e44bc',
            str(ADAPTER / 'scripts/handshake.py'):
                'df921e49e0a11ce8b8c73d71e23e49a16169c577e575f3cc01d3e4b6c0fb7857',
            str(ROOT / 'scripts/marker_worker.py'):
                '305327f680c834a5b6ede03d2e187a340d55227ea0e633e3e4a263177a34b99b'
        },
        'real_faults_injected': 0,
        'new_production_attempts_created': 0,
        'host_reboot_tested': False,
        'full_size_root_archive_tested': False,
        'formal_production_authorized': False,
    }
    if any(result['source_sha256'].get(path) != expected
           for path, expected in result['worker2_code_sha256_verified'].items()):
        raise RuntimeError('Worker2 source hash no longer matches Master')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'],
                      'source_files': len(source), 'evidence_files': len(EVIDENCE),
                      'manifest_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
