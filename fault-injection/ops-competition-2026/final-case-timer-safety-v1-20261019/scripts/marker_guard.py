#!/usr/bin/env python3
"""Independent Master guard using the same watch loop as the future controller."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
sys.path.insert(0, str(OPS / 'sf013-adapter-v3-20261019/scripts'))
from timer_guard import watch_controller
from marker_controller import remote


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--registration-sha', required=True)
    args = parser.parse_args()
    if sha(args.registration) != args.registration_sha:
        raise RuntimeError('MARKER_GUARD_REGISTRATION_HASH')
    reg = json.loads(args.registration.read_text())
    reg['registration_sha256'] = args.registration_sha
    root = args.registration.parent
    receipt = watch_controller(
        reg['controller_unit'], root / 'controller-terminal.json',
        root / 'controller-started.json',
        datetime.fromisoformat(reg['planned_utc']).timestamp() + 5,
        lambda: remote(reg, 'cancel'), poll_seconds=0.25, startup_grace=5)
    receipt.update(run_id=reg['run_id'],
                   at_utc=datetime.now(timezone.utc).isoformat())
    output = root / 'guard-terminal.json'
    with output.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    if receipt['worker_cancel'].get('status') != 'ATTEMPT_TIMER_REVOKED_AND_INACTIVE':
        raise RuntimeError('MARKER_GUARD_CANCEL_UNVERIFIED')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
