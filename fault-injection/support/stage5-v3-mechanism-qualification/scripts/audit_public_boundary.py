"""Scan only candidate public telemetry for private fault/GT fields."""

import argparse
import hashlib
import json
from pathlib import Path


FORBIDDEN = {
    'root_layer': b'"root_layer"',
    'root_entity': b'"root_entity"',
    'fault_target': b'"fault_target"',
    'fault_family': b'"fault_family"',
    'injection_command': b'"injection_command"',
    'private_injection_evidence': b'"private_injection_evidence"',
    'adapter_state': b'"adapter_state"',
    'netem_command': b'"netem"',
    'sigstop_command': b'"SIGSTOP"',
}


def scan(path):
    found = set()
    tail = b''
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            view = tail + chunk
            found.update(name for name, token in FORBIDDEN.items()
                         if token in view)
            tail = view[-max(len(token) for token in FORBIDDEN.values()):]
    return sorted(found)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite of public boundary audit')
    root = args.run / 'capture-pipeline/captures'
    if not root.is_dir():
        raise ValueError('Captured public telemetry is missing')
    files = sorted(path for path in root.rglob('*.json') if path.is_file())
    files.extend(path for path in (args.run / 'route-proof.json',)
                 if path.is_file())
    if not files:
        raise ValueError('No candidate public files to audit')
    violations = []
    for path in files:
        for label in scan(path):
            violations.append({'path': str(path.relative_to(args.run)),
                               'forbidden_field': label})
    manifests = [root / phase / 'capture-manifest.json'
                 for phase in ('before', 'during', 'after')]
    if any(not path.is_file() for path in manifests):
        raise ValueError('Public capture manifest is incomplete')
    result = {
        'status': 'NO_PRIVATE_LEAK_DETECTED' if not violations else 'PRIVATE_FIELD_FOUND',
        'files_scanned': len(files), 'violations': violations,
        'capture_manifest_sha256': {phase: hashlib.sha256(path.read_bytes()).hexdigest()
            for phase, path in zip(('before', 'during', 'after'), manifests)},
        'private_runtime_and_GT_not_scanned_as_public': True,
        'formal_public_bundle_created': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'],
                      'files_scanned': result['files_scanned'],
                      'violations': len(violations)}))
    if violations:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
