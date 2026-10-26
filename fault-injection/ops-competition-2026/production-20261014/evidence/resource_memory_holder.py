#!/usr/bin/env python3
"""Private bounded allocator started only after its parent assigns a cgroup."""

import argparse
import signal
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bytes', required=True, type=int)
    parser.add_argument('--maximum-seconds', type=int, default=330)
    args = parser.parse_args()
    if not 32 * 1024 * 1024 <= args.bytes <= 16 * 1024 ** 3 or (
            args.maximum_seconds != 330):
        raise ValueError('Unbounded Stage5 memory allocation')
    if sys.stdin.buffer.read(1) != b'1':
        raise ValueError('Allocator did not receive its parent start token')
    chunks = []
    remaining = args.bytes
    while remaining:
        size = min(1024 * 1024, remaining)
        chunk = bytearray(size)
        chunk[::4096] = b'\x01' * len(chunk[::4096])
        chunks.append(chunk)
        remaining -= size
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    deadline = time.monotonic() + args.maximum_seconds
    while time.monotonic() < deadline:
        time.sleep(1)
    del chunks


if __name__ == '__main__':
    main()
