#!/usr/bin/env python3
"""Hash immutable Phase 4 evidence without reading Stage5-v2 case data."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path('/root/cross-layer-rehearsal-phase4')
STAGE5_FREEZE = Path('/root/stage5-v2-cross-layer-rca/freeze-manifest.json')
EXPECTED_STAGE5_FREEZE = '6ebafa764b904950c00904d06cebae68c66727a8cf703158aa259bb728cd6452'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    output = ROOT / 'archive-manifest.json'
    if output.exists():
        raise ValueError('Refusing overwrite')
    stage5_hash = sha(STAGE5_FREEZE)
    if stage5_hash != EXPECTED_STAGE5_FREEZE:
        raise ValueError('Frozen Stage5-v2 manifest hash changed')
    files = {}
    for path in sorted(ROOT.rglob('*')):
        if (not path.is_file() or path == output or '__pycache__' in path.parts or
                path.suffix == '.pyc'):
            continue
        relative = str(path.relative_to(ROOT))
        files[relative] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    result = {'phase': 'cross-layer-rehearsal-phase4',
              'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'stage5_v2_freeze_manifest_sha256': stage5_hash,
              'stage5_v2_test_accessed': False,
              'stage5_v3_formal_case_created': False,
              'file_count': len(files), 'files': files}
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': 'PHASE4_ARCHIVE_HASHED', 'files': len(files),
                      'manifest_sha256': sha(output)}))


if __name__ == '__main__':
    main()
