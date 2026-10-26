#!/usr/bin/env python3
"""Record independent rehearsal design/code provenance before any fault."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml


ROOT=Path('/root/cross-layer-observability-control-rehearsal')
FILES=[
    'design/rehearsal-plan-v2.yaml','design/fault-adapters.yaml',
    'design/pod-identity-rollout-plan.json',
    'scripts/workload_generator_rehearsal.py','scripts/fixed_control_load.py',
    'scripts/run_load_pair.py','scripts/host_fault.py','scripts/network_fault.py',
    'scripts/probe_network.py','scripts/build_preflight.py',
    'scripts/verify_business_routes.py','scripts/orchestrate_rehearsal.py',
    'scripts/audit_trace_identity.py','scripts/analyze_control_run.py']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    path=ROOT/'design/registered-rehearsal-manifest.json'
    if path.exists():raise ValueError('Rehearsal design already registered')
    design=yaml.safe_load((ROOT/'design/rehearsal-plan-v2.yaml').read_text())
    adapters=yaml.safe_load((ROOT/'design/fault-adapters.yaml').read_text())
    ids=[item['id'] for item in design['experiments']]
    adapter_ids=[item['id'] for key in ('service_pod_experiments','network_experiments')
                 for item in adapters[key]]
    if len(ids)!=6 or len(set(ids))!=6 or set(ids)!=set(adapter_ids):
        raise ValueError('Six unique experiment IDs must match fault adapter plan')
    streams=design['workload']['control_streams']
    if {row['name'] for row in streams}!={'image_asset','product_detail','cart_view'}:
        raise ValueError('Fixed business control set changed')
    if (design['network_controls']['minimum_valid_requests_per_60_seconds']!=30 or
            design['network_controls']['minimum_usable_spans_per_60_seconds']!=30):
        raise ValueError('Unexpected control sample rule')
    names={name:sha(ROOT/name) for name in FILES}
    result={'registered_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'INDEPENDENT_REHEARSAL_REGISTERED',
        'faults_injected':0,'stage5_v3_cases_created':0,'stage5_v2_test_reads':0,
        'ids':ids,'seeds':{row['id']:row['seed'] for row in design['experiments']},
        'control_rates_per_second':{row['name']:row['rate_per_second'] for row in streams},
        'hashes':names}
    path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'experiment_ids':ids,
                      'manifest_sha256':sha(path)}))


if __name__=='__main__':main()
