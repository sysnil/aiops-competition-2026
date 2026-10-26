"""Final read-back and isolation audit for the generated QA package."""

from __future__ import annotations

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


def main(root=ROOT):
    manifest = json.loads((root / 'sha256-manifest.json').read_text())
    files = manifest['files']
    if len(files) < 40:
        raise ValueError('Package manifest is unexpectedly small')
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha(path) != expected:
            raise ValueError(f'Package file failed read-back: {name}')
    public_questions = sorted((root / 'public/questions').glob('Q*.json'))
    if len(public_questions) != 10:
        raise ValueError('Formal Public question count is not ten')
    forbidden = ('three_gold_answers', 'canonical_chart_data',
                 'DRAFT_PENDING_HUMAN_REVIEW', 'ServicePause',
                 'PodMemoryStress', 'private/reference-charts')
    for path in public_questions:
        text = path.read_text()
        if any(word in text for word in forbidden):
            raise ValueError(f'Private field or mechanism leaked into {path.name}')
    if not (root / 'public/PLAYER_MANUAL_QUERY_EXAMPLES.md').is_file():
        raise ValueError('Player manual example chapter is missing')
    return {'status': 'PACKAGE_READBACK_PASS', 'hashed_files': len(files),
            'formal_questions': len(public_questions),
            'practice_examples': len(list((root / 'public/practice-examples').glob('EX-P*.json'))),
            'formal_question_leak_scan': 'PASS'}


if __name__ == '__main__':
    print(json.dumps(main(), indent=2))
