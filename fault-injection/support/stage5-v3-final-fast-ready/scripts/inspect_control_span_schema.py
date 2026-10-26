#!/usr/bin/env python3
"""Read-only diagnostic of normal-control UID and trace parent linkage."""

from collections import Counter
import importlib.util
import json
from pathlib import Path

from clean_protocol import save_new, sha


RUN = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-CLEAN-20261013-S1')
SOURCE = Path('/root/stage5-v3-clean-gate-v2/scripts/trace_identity.py')
OUTPUT = Path('/root/stage5-v3-final-fast-ready/results/control-span-schema-diagnostic.json')


def main():
    spec = importlib.util.spec_from_file_location('trace_identity', SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod.span_map(RUN / 'capture-pipeline/trace-final/before')
    spans = {key: item[1] for key, item in rows.items()}
    details = {}
    for service, parent in (('image-provider', 'frontend-proxy'),
                            ('cart', 'frontend'), ('payment', 'checkout')):
        counts = Counter()
        examples = []
        uid_counts = Counter()
        for span in spans.values():
            if span['process']['serviceName'] != service or (
                    span.get('tag') or {}).get('span@kind') != 'server':
                continue
            counts['server_total'] += 1
            uid = {r.get('key'): r.get('value') for r in span[
                'process'].get('tags') or []}.get('k8s.pod.uid')
            uid_counts[uid or 'UNATTRIBUTED'] += 1
            found = False
            for ref in span.get('references') or []:
                p = spans.get((ref.get('traceID', '').lower(),
                               ref.get('spanID', '').lower()))
                if p and p['process']['serviceName'] == parent and (
                        p.get('tag') or {}).get('span@kind') == 'client':
                    counts['expected_client_parent'] += 1
                    counts['expected_client_duration_positive'] += p['duration'] > 0
                    found = True
                    break
                elif p:
                    counts['other_parent_process_or_kind'] += 1
                else:
                    counts['parent_unavailable_in_phase'] += 1
            if not found and len(examples) < 5:
                examples.append({'trace_id': span['traceID'],
                    'span_id': span['spanID'],
                    'references': span.get('references') or [],
                    'uid': uid})
        details[service] = {'counts': dict(counts),
                            'uid_counts': dict(uid_counts),
                            'unlinked_examples_public_only': examples}
    value = {'status': 'PUBLIC_SPAN_SCHEMA_DIAGNOSTIC_NOT_RESCORING',
        'run_id': RUN.name, 'phase': 'before', 'details': details,
        'trace_final_manifest_sha256': sha(RUN /
            'capture-pipeline/trace-final/before/final-manifest.json'),
        'source_sha256': sha(SOURCE),
        'original_clean_score_changed': False,
        'private_gt_read': False}
    save_new(OUTPUT, value)
    print(json.dumps({'status': value['status'], 'summary': {
        name: row['counts'] for name, row in details.items()}}))


if __name__ == '__main__': main()
