#!/usr/bin/env python3
"""Apply the unchanged Action revoke operation in the isolated Master VM."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path('/home/liuzihan.guest/sf-vm')
sys.path.insert(0, str(ROOT / 'scripts'))

from action_decision import revoke


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--action-id', required=True)
    parser.add_argument('--manifest-sha', required=True)
    args = parser.parse_args()
    directory = ROOT / 'runtime' / args.run_id / args.action_id
    identity = {'run_id': args.run_id, 'action_id': args.action_id,
                'manifest_sha256': args.manifest_sha}
    print(json.dumps(revoke(directory, identity, 'ISOLATED_VM_TEST_REVOKE')))


if __name__ == '__main__':
    main()
