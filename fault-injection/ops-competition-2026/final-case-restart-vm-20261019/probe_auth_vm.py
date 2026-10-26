#!/usr/bin/env python3
"""VM-only one-shot diagnostic of the forced authorization response."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/home/liuzihan.guest/sf-vm')
sys.path.insert(0, str(ROOT / 'scripts'))

import marker_vm
import auth_protocol
import worker_auth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    parser.add_argument('--planned-epoch', type=float, required=True)
    args = parser.parse_args()
    action = json.loads(args.registration.read_text())
    binding = json.loads((args.registration.parent / 'auth-binding.json').read_text())
    planned = args.planned_epoch
    time.sleep(max(0, planned - time.time() + .1))
    req = auth_protocol.request(
        binding['run_id'], binding['action_id'],
        binding['manifest_sha256'], binding['capability'],
        binding['worker_host'], binding['worker_boot_id'])
    result = subprocess.run(worker_auth.ssh_command(),
                            input=auth_protocol.canonical(req) + b'\n',
                            capture_output=True, timeout=3)
    print(json.dumps({'returncode': result.returncode,
                      'stdout': result.stdout.decode(errors='replace'),
                      'stderr': result.stderr.decode(errors='replace'),
                      'offset_seconds': time.time() - planned}))


if __name__ == '__main__':
    main()
