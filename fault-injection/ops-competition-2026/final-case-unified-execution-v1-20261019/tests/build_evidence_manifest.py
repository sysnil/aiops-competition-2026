#!/usr/bin/env python3
"""Hash this independent candidate's source, contract, and harmless evidence."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
items = sorted([*root.glob('scripts/*.py'), *root.glob('design/*.json'),
                root / 'tests/test_unified_safety.py',
                root / 'tests/live_marker_rehearsal.py',
                root / 'results/live-marker-integration.json'])
if any(not path.is_file() for path in items):
    raise RuntimeError('EVIDENCE_INPUT_MISSING')
manifest = {
    'status': 'CODE_AND_HARMLESS_MARKER_EVIDENCE_ONLY',
    'production_status': 'PRODUCTION_BLOCKED',
    'created_utc': datetime.now(timezone.utc).isoformat(),
    'files': {str(path.relative_to(root)):
              hashlib.sha256(path.read_bytes()).hexdigest() for path in items},
}
output = root / 'results/source-evidence-manifest.json'
output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
print(output)
