#!/usr/bin/env python3
"""Register SF-004 control check before reviewing either new window."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


ROOT = Path('/root/ops-competition-2026')
REAL = ROOT / 'sf004-real-adapter-v2-20261016'
FORMAL = ROOT / 'sf004-formal-v2-20261016'
DATA = Path('/data/stage5-v3/ops-competition-2026/collector-debug-repair-20261016/runs-v6')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    contract = FORMAL / 'design/http-home-admission-draft.yaml'
    evaluator = REAL / 'scripts/assess_control_stability.py'
    policy = yaml.safe_load(contract.read_text())
    controls = policy['normal_controls']
    plan = {
        'status': 'PREREGISTERED_UNCHANGED_SF004_CONTROL_RULE',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'admission_contract': str(contract),
        'admission_contract_sha256': sha(contract),
        'evaluator_sha256': sha(evaluator),
        'anchor_offset_seconds': policy['fault_window']['start_offset_seconds'],
        'during_seconds': policy['fault_window']['duration_seconds'],
        'before_seconds': policy['public_effect']['before_window_seconds'],
        'after_seconds': policy['public_effect']['after_window_seconds'],
        'maximum_p95_ratio': controls['maximum_after_to_before_p95_ratio'],
        'minimum_control_trace_ids': {
            'image': {'before': controls['require_image_successes_before_after'],
                      'during': controls['require_image_successes_during'],
                      'after': controls['require_image_successes_before_after']},
            'cart': {'before': controls['require_cart_successes_before_after'],
                     'during': controls['require_cart_successes_during'],
                     'after': controls['require_cart_successes_before_after']},
        },
        'runs': {role: {'run_dir': str(DATA / role), 'seed': seed}
                 for role, seed in (('VAL1', 741603), ('VAL2', 741604))},
    }
    output = REAL / 'design/control-stability-prereg.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(plan, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({'status': plan['status'], 'path': str(output), 'sha256': sha(output)}))
