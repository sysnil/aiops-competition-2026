#!/usr/bin/env python3
"""One read-only thirty-second real Metrics/Logs/Traces capture smoke."""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


DATA = Path('/data/stage5-v3/ops-competition-2026/rehearsals')
CAP = Path('/root/ops-competition-2026/low-stable-v4-20261017/capture/capture_live_c2.py')


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def main():
    now = time.time()
    root = DATA / ('final-case-capture-smoke-' + str(int(now)))
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    output = root / 'captures/during'
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf',
               A04_CAPTURE_ROOT=str(root),
               A04_BUDGET_LEDGER=str(root / 'budget-ledger'),
               A04_JSON_CAP_BYTES=str(2 * 1024**3))
    result = subprocess.run(['/usr/bin/python3', '-B', str(CAP),
        '--phase', 'during', '--start-utc', iso(now - 35),
        '--end-utc', iso(now - 5), '--trace-source', 'JAEGER_OPENSEARCH_STORAGE',
        '--output', str(output)], env=env, capture_output=True, text=True,
        timeout=120)
    evidence = {'status': 'CAPTURE_SMOKE_PASS' if result.returncode == 0 else
                'CAPTURE_SMOKE_FAILED', 'run_dir': str(root),
                'stdout': result.stdout[-1000:], 'stderr': result.stderr[-1500:]}
    manifest = output / 'capture-manifest.json'
    if manifest.is_file():
        observed = json.loads(manifest.read_text())
        evidence.update(traces=observed['trace_records'],
                        spans=observed['span_records'],
                        logs=observed['log_records'],
                        metrics_files=len(list((output / 'metrics').glob('*.json'))))
    (root / 'smoke-result.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))
    if evidence['status'] != 'CAPTURE_SMOKE_PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
