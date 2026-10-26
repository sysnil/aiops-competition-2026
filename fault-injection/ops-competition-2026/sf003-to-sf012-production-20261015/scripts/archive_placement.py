#!/usr/bin/env python3
"""Choose two independent post-case replicas without deleting prior archives."""

import argparse
import json
from pathlib import Path


FLOOR = 5_000_000_000
WORKERS = ('worker2', 'worker3')
OFFSITE = 'contest51'


def choose(free_bytes, forecast_bytes, *, offsite_verified, private_sealed,
           mac_relay_available):
    """This is an archive placement gate, never a fault-execution permission."""
    required = WORKERS
    if any(not isinstance(free_bytes.get(host), int) or free_bytes[host] < 0
           for host in required):
        return {'status': 'ARCHIVE_PLACEMENT_BLOCKED', 'reason': 'INCOMPLETE_CAPACITY',
                'fault_permission': False}
    if not isinstance(forecast_bytes, int) or forecast_bytes < 0:
        return {'status': 'ARCHIVE_PLACEMENT_BLOCKED', 'reason': 'INVALID_FORECAST',
                'fault_permission': False}
    eligible = [host for host in WORKERS
                if free_bytes[host] - forecast_bytes >= FLOOR]
    offsite_free = free_bytes.get(OFFSITE)
    offsite_has_capacity = (isinstance(offsite_free, int) and
                            offsite_free - forecast_bytes >= FLOOR)
    offsite_ok = (offsite_verified and private_sealed and mac_relay_available
                  and offsite_has_capacity)
    if offsite_ok and eligible:
        worker = max(eligible, key=lambda host: free_bytes[host] - forecast_bytes)
        return {'status': 'TWO_REPLICA_PLACEMENT_AVAILABLE',
                'replicas': [worker, OFFSITE], 'reason': 'OFFSITE_AND_HEALTHIEST_WORKER',
                'remaining_worker_bytes': free_bytes[worker] - forecast_bytes,
                'remaining_offsite_bytes': offsite_free - forecast_bytes,
                'fault_permission': False}
    if len(eligible) == 2:
        return {'status': 'TWO_REPLICA_PLACEMENT_AVAILABLE',
                'replicas': list(WORKERS),
                'reason': 'OFFSITE_UNAVAILABLE_USE_EXISTING_WORKERS',
                'fault_permission': False}
    return {'status': 'ARCHIVE_PLACEMENT_BLOCKED',
            'reason': 'NO_TWO_INDEPENDENT_REPLICAS_ABOVE_FLOOR',
            'offsite_blockers': {
                'transport_verified': not offsite_verified,
                'private_sealed': not private_sealed,
                'mac_relay_available': not mac_relay_available,
                'offsite_capacity': not offsite_has_capacity,
            }, 'fault_permission': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--forecast-bytes', type=int, required=True)
    parser.add_argument('--offsite-verified', action='store_true')
    parser.add_argument('--private-sealed', action='store_true')
    parser.add_argument('--mac-relay-available', action='store_true')
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    free = {host: snapshot['free_bytes'].get(host) for host in (*WORKERS, OFFSITE)}
    print(json.dumps(choose(free, args.forecast_bytes,
        offsite_verified=args.offsite_verified,
        private_sealed=args.private_sealed,
        mac_relay_available=args.mac_relay_available)))


if __name__ == '__main__':
    main()
