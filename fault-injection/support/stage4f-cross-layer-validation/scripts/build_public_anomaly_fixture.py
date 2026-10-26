"""Build a reproducible public-only anomaly fixture for endpoint readiness."""

import argparse
import hashlib
import json
from pathlib import Path
import uuid


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(source, output, manifest):
    if output.exists() or manifest.exists():
        raise ValueError('Refusing to overwrite a public fixture')
    bundle = json.loads(source.read_text())
    if (bundle.get('schema_version') != 1 or bundle.get('public_only') is not True or
            bundle.get('variant') != 'B' or 'case_id' in bundle):
        raise ValueError('Fixture source must be an anonymous public Variant B')
    if any(signal.get('abnormal') is True
           for modalities in bundle.get('signals', {}).values()
           for rows in modalities.values() for signal in rows):
        raise ValueError('Controlled fixture requires a clean public source')
    eligible = []
    for entity, modalities in bundle['signals'].items():
        if not entity.startswith('SERVICE/'):
            continue
        count = sum(signal.get('observed') is True for rows in modalities.values()
                    for signal in rows)
        if count >= 4:
            eligible.append((-count, entity))
    if not eligible:
        raise ValueError('No service has four public observed signal families')
    _, entity = min(eligible)
    changed = 0
    for rows in bundle['signals'][entity].values():
        for signal in rows:
            if signal.get('observed') is True:
                signal['abnormal'] = True
                changed += 1
    bundle['public_case_id'] = str(uuid.uuid4())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=True, separators=(',', ':')) + '\n')
    result = {'status': 'CONTROLLED_PUBLIC_ANOMALY_FIXTURE',
              'purpose': 'GT-free endpoint executable smoke only; never a Stage4F case',
              'source_bundle_sha256': sha256(source),
              'fixture_bundle_sha256': sha256(output),
              'selection_rule': 'SERVICE candidate with most observed signals, then entity name',
              'selected_public_entity': entity, 'signals_marked_abnormal': changed,
              'public_case_id': bundle['public_case_id']}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    result = build(args.source, args.output, args.manifest)
    print(json.dumps({'status': result['status'],
                      'signals_marked_abnormal': result['signals_marked_abnormal']}))


if __name__ == '__main__':
    main()
