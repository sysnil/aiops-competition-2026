#!/usr/bin/env python3
"""Supply one endpoint API key from a hidden TTY prompt to the isolated smoke."""

import argparse
import getpass
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


WRAPPER = Path(__file__).resolve().with_name('run_openrca_isolated.py')


def chat_endpoint(base_url):
    base_url = base_url.strip()
    parsed = urlparse(base_url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError('A credential-free HTTPS base URL is required')
    path = parsed.path.rstrip('/')
    if path.endswith('/chat/completions'):
        return base_url.rstrip('/')
    if path not in ('/api/plan/v3', '/v1'):
        raise ValueError('Expected an OpenAI-compatible /v1 or Ark /api/plan/v3 base URL')
    return base_url.rstrip('/') + '/chat/completions'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-output', type=Path, required=True)
    args = parser.parse_args()
    endpoint = chat_endpoint(args.base_url)
    if args.output.exists() or args.audit_output.exists():
        raise ValueError('Refusing to overwrite a blind smoke artifact')
    key = getpass.getpass('Endpoint API key: ')
    if not key:
        raise ValueError('Endpoint API key was empty')
    env = os.environ.copy()
    env.update({'OPENRCA_XL_ENDPOINT_URL': endpoint,
                'OPENRCA_XL_MODEL': args.model,
                'OPENRCA_XL_API_KEY': key})
    argv = [sys.executable, str(WRAPPER), '--input', str(args.input),
            '--output', str(args.output), '--audit-output', str(args.audit_output)]
    os.execve(sys.executable, argv, env)


if __name__ == '__main__':
    main()
