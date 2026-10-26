#!/usr/bin/env python3
"""Small, explicitly synthetic raw tree for the dual-archive transport test."""

import json
from pathlib import Path


RUN_ID = 'S5V3FFR-CLEAN-OFFLINE-ARCHIVE-20261013'
ROOT = Path('/data/stage5-v3/final-fast-ready/runs') / RUN_ID


def write(relative, value):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream)
        stream.write('\n')


def main():
    if ROOT.exists():
        raise ValueError('Synthetic archive fixture already exists')
    ROOT.mkdir(parents=True)
    write('schedule.json', {'run_id': RUN_ID, 'synthetic_offline_only': True,
                            'fault_injected': False})
    write('run-result.json', {'status': 'SYNTHETIC_OFFLINE_ARCHIVE_FIXTURE',
                              'fault_injected': False})
    write('load_pair/state-machine/sessions.jsonl', {'steps': [],
                                                      'synthetic_offline_only': True})
    write('load_pair/controls/image_asset.jsonl', {'synthetic_offline_only': True})
    write('load_pair/controls/cart_view.jsonl', {'synthetic_offline_only': True})
    write('capture-pipeline/pipeline-result.json', {'status': 'CAPTURE_PIPELINE_PASS',
                                                     'synthetic_offline_only': True})
    write('capture-pipeline/captures/during/metrics/cpu.json',
          {'status': 'success', 'data': {'result': []}, 'synthetic_offline_only': True})
    write('capture-pipeline/captures/during/logs/page-0000.json',
          {'hits': {'hits': []}, 'synthetic_offline_only': True})
    write('capture-pipeline/captures/during/trace-storage-pages/page-0000.json',
          {'hits': {'hits': []}, 'synthetic_offline_only': True})
    print(json.dumps({'status': 'OFFLINE_ARCHIVE_FIXTURE_CREATED',
                      'path': str(ROOT), 'formal_case': False}))


if __name__ == '__main__': main()
