"""Hash every final deliverable after generation and validation have stopped."""

import hashlib
import json
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    files = {}
    for path in sorted(ROOT.rglob('*')):
        if (path.is_file() and path.name != 'sha256-manifest.json' and
                '__pycache__' not in path.parts and path.suffix != '.pyc'):
            files[str(path.relative_to(ROOT))] = sha(path)
    (ROOT / 'sha256-manifest.json').write_text(json.dumps({
        'version': 'semifinal-query-visualization-candidate-v1',
        'hash_algorithm': 'SHA-256', 'files': files},
        ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'ALL_DELIVERABLES_HASHED', 'count': len(files)}))


if __name__ == '__main__':
    main()
