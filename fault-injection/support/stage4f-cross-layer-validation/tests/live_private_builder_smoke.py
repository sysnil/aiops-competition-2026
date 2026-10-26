#!/usr/bin/env python3
"""Exercise private archiving with real public captures and synthetic fault facts."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from build_private_case import build


ROOT = Path('/root/stage4f-cross-layer-validation')
READINESS = ROOT / 'readiness'
CAPTURE = READINESS / 'node-metric-formal-smoke-2026-10-03'
HOSTS = {'F01': ('k8s-worker1', 'k8s-worker2'),
         'F02': ('k8s-worker2',), 'F03': ('k8s-worker1',),
         'F04': ('k8s-worker3',)}


def synthetic_after(before, during, output):
    shutil.copytree(before, output, copy_function=os.link)
    manifest_file = output / 'capture-manifest.json'
    manifest = json.loads(manifest_file.read_text())
    manifest_file.unlink()
    start = datetime.fromisoformat(json.loads((during / 'capture-manifest.json').read_text())[
        'window']['end_utc']) + timedelta(seconds=1)
    end = start + timedelta(seconds=600)
    manifest['phase'] = 'after'
    manifest['window'] = {'start_utc': start.isoformat(), 'end_utc': end.isoformat(),
                          'start_unix_ns': round(start.timestamp() * 1e9),
                          'end_unix_ns': round(end.timestamp() * 1e9)}
    manifest['metadata_observed_utc'] = end.isoformat()
    manifest_file.write_text(json.dumps(manifest))


def main():
    before, during = CAPTURE / 'before', CAPTURE / 'during'
    with tempfile.TemporaryDirectory(prefix='private-builder-synthetic-',
                                     dir=str(READINESS)) as temporary:
        temp = Path(temporary)
        after = temp / 'after'
        synthetic_after(before, during, after)
        recovery = temp / 'recovery.json'
        recovery.write_text(json.dumps({'status': 'RECOVERY_GATE_PASS'}))
        results = {}
        for case, hosts in HOSTS.items():
            target = temp / 'preflight' / case
            shutil.copytree(READINESS / ('node-metric-formal-preflight-' + case), target)
            start = datetime.fromisoformat(json.loads((during / 'capture-manifest.json').read_text())[
                'window']['start_utc'])
            end = start + timedelta(seconds=300)
            states = []
            for host in hosts:
                path = temp / (case + '-' + host + '-synthetic.json')
                path.write_text(json.dumps({'case_id': case, 'host': host,
                                            'status': 'CLEANED',
                                            'start_at_utc': start.isoformat(),
                                            'cleanup_at_utc': end.isoformat()}))
                states.append(path)
            with patch('build_private_case.verify_preflight', return_value=None):
                results[case] = build(
                    temp, case, before, during, after,
                    READINESS / ('node-metric-formal-conversion-' + case),
                    recovery, states,
                    CAPTURE / 'probe.jsonl' if case == 'F04' else None,
                    temp / 'evaluator_gt' / case)
    print(json.dumps({'status': 'SYNTHETIC_PRIVATE_ARCHIVE_SMOKE_COMPLETE',
                      'not_formal_cases': True, 'results': results}))


if __name__ == '__main__':
    main()
