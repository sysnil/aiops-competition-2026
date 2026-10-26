#!/usr/bin/env python3
"""Local index of small, retained final-gate evidence and planning sources."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


project = Path(__file__).resolve().parents[2]
prep = project / 'final-case-qualification-prep-20261019'
last = project / 'final-case-last-gate-v3-20261019'
formal = project / 'final-case-formal-entry-v3-20261019'
files = sorted([
    *prep.glob('scripts/*.py'), *prep.glob('design/*.md'),
    *prep.glob('results/*.json'), *last.glob('results/*.json'),
    *formal.glob('results/sf-0*.json'),
])
files = [path for path in files if path.name != 'last-gate-evidence-index.json']
index = {'status': 'PRE_PRODUCTION_EVIDENCE_RETAINED',
         'production_status': 'PRODUCTION_BLOCKED',
         'real_faults_injected': 0, 'formal_attempts_created': 0,
         'at_utc': datetime.now(timezone.utc).isoformat(),
         'files': {str(path.relative_to(project)):
                   hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
output = prep / 'results/last-gate-evidence-index.json'
output.write_text(json.dumps(index, indent=2, sort_keys=True) + '\n')
print(output)
