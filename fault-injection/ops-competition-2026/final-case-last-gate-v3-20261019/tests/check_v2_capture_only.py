#!/usr/bin/env python3
"""Read-only diagnosis of the preserved v2 six-chunk capture."""

import json
import sys
from pathlib import Path

CAP = Path('/root/ops-competition-2026/low-stable-v4-20261017/capture')
sys.path.insert(0, str(CAP))
from trace_identity import span_map

run = Path('/data/stage5-v3/ops-competition-2026/rehearsals/'
           'SF-017-ATTEMPT-MARKERTEST-7FEA8BC5-full1800')
proxies = set()
indices = []
for index in range(6):
    root = run / 'capture' / f'chunk-{index:02d}'
    manifest = json.loads((root / 'captures/during/capture-manifest.json').read_text())
    indices.append(manifest['trace_storage_indices'])
    spans = span_map(root / 'trace-final/during')
    proxies.update(key[0].lower() for key, (_, span) in spans.items()
                   if span.get('process', {}).get('serviceName') == 'frontend-proxy'
                   and span.get('tag', {}).get('span@kind') == 'server')
requests = [json.loads(line) for line in
            (run / 'controls/control-events-live.jsonl').read_text().splitlines()]
ids = [row['trace_id'].lower() for row in requests if row.get('trace_id')]
missing = [ident for ident in ids if ident not in proxies]
print(json.dumps({'control_requests': len(ids), 'matched_proxy_traces': len(ids)-len(missing),
                  'missing_count': len(missing), 'missing_sample': missing[:5],
                  'same_trace_source_indices': all(item == indices[0] for item in indices)}))
