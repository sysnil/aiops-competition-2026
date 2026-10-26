#!/usr/bin/env python3
"""VM-only endpoint wiring for the unchanged v1 Master authorizer core."""

import sys
from pathlib import Path

ROOT = Path('/home/liuzihan.guest/sf-vm')
sys.path.insert(0, str(ROOT / 'scripts'))

import master_authorizer

master_authorizer.RUNTIME = ROOT / 'runtime'
master_authorizer.authorize.__kwdefaults__['state_root'] = ROOT / 'runtime'
master_authorizer.SIGNING_KEY = ROOT / 'master-signing.pem'
master_authorizer.WORKERS = {'k8s-worker2': '192.168.5.2'}
master_authorizer.CASES = {'SF-013': {'k8s-worker2'}}

if __name__ == '__main__':
    master_authorizer.main()
