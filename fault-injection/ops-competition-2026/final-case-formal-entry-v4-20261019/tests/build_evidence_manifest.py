#!/usr/bin/env python3
"""Hash candidate source and harmless test evidence without self-reference."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


root = Path(__file__).resolve().parents[1]
files = sorted([*root.glob('scripts/*.py'), *root.glob('design/*.json'),
                *root.glob('tests/*.py'), *root.glob('results/*.json')])
files = [path for path in files if path.name != 'evidence-manifest.json']
result = {'status': 'CODE_AND_HARMLESS_TEST_EVIDENCE_ONLY',
          'production_status': 'PRODUCTION_BLOCKED',
          'created_utc': datetime.now(timezone.utc).isoformat(),
          'sha256': {str(path.relative_to(root)):
                     hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in files}}
output = root / 'results/evidence-manifest.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
print(output)
