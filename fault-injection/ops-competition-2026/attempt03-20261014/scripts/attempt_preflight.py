#!/usr/bin/env python3
"""Case observer allowlist only; unchanged competition environment safety checks."""

from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request


AUDIT = Path('/data/stage5-v3/ops-competition-2026/audits/COMP-SF01-AUDIT-20261013-0953')
SAFETY = Path('/root/stage5-v3-clean-gate-v2/scripts/global_safety.py')
WRAPPER = Path('/root/stage5-v3-fast-ready/scripts/assess_precanary_safety.py')
POLICY = Path('/root/stage5-v3-clean-gate-v2/design/global-safety-policy-draft.json')
KUBE = ['/usr/bin/kubectl', '--kubeconfig=/etc/kubernetes/admin.conf']
PROM = 'http://10.97.14.138:9090'


def read(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def kube(*args):
    return subprocess.check_output(KUBE + list(args), text=True, timeout=30)


def source_times(metric):
    query = urllib.parse.urlencode({'query':'timestamp(' + metric + ')'})
    with urllib.request.urlopen(PROM + '/api/v1/query?' + query, timeout=15) as response:
        value = json.load(response)
    if value.get('status') != 'success': raise RuntimeError('Prometheus source-time query failed')
    return [{'node': row['metric'].get('k8s_node_name'),
             'source_timestamp': float(row['value'][1]),
             'evaluation_timestamp': float(row['value'][0])}
            for row in value['data']['result']]


def main():
    global AUDIT
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit-dir', type=Path, default=AUDIT)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--expected-observation-unit')
    args = parser.parse_args()
    if args.expected_observation_unit and not re.fullmatch(r'ops-case-sf-pilot-01-attempt-03(?:-rehearsal|-exit-test)?\.service',args.expected_observation_unit):
        raise RuntimeError('Only the registered no-fault observation unit may be present before injection')
    AUDIT = args.audit_dir
    if args.refresh:
        source = Path('/root/stage5-v3-clean-gate-v2/scripts/collect_snapshot.py')
        spec = importlib.util.spec_from_file_location('readonly_snapshot', source)
        collect = importlib.util.module_from_spec(spec); spec.loader.exec_module(collect)
        snapshot = collect.collect()
        with (AUDIT / 'global-safety-raw.json').open('x') as stream:
            json.dump(snapshot,stream,indent=2);stream.write('\n')
    output = AUDIT / 'phase-a-decision.json'
    if output.exists(): raise RuntimeError('Immutable preflight result already exists')
    raw = read(AUDIT / 'global-safety-raw.json')
    residue, hosts, policy = read(AUDIT / 'residue.json'), read(AUDIT / 'hosts.json'), read(POLICY)
    extra, evidence = {}, {}
    now = datetime.now(timezone.utc)
    residue_age = (now - datetime.fromisoformat(residue['at_utc'])).total_seconds()
    extra['fresh_five_host_residue'] = residue['status'] == 'HOST_RESIDUE_PASS' and 0 <= residue_age <= 180
    deployments = json.loads(kube('-n','otel-demo','get','deployments','-o','json'))['items']
    ready = all(d['status'].get('readyReplicas',0) == d['spec'].get('replicas',1) for d in deployments)
    raw['components'].update({'five_host_residue':'PASS' if extra['fresh_five_host_residue'] else 'FAIL',
        'no_active_fault':'PASS' if extra['fresh_five_host_residue'] else 'FAIL',
        'no_mass_pod_restart':'PASS' if ready else 'FAIL',
        'independent_watchdog':'NOT_VERIFIED_FOR_THIS_COMPETITION_ATTEMPT'})
    extra['control_plane_readyz'] = kube('get','--raw','/readyz').strip() == 'ok'
    coredns = json.loads(kube('-n','kube-system','get','deployment','coredns','-o','json'))
    extra['coredns_ready'] = coredns['status'].get('readyReplicas',0) == coredns['spec'].get('replicas',1) > 0
    for name in policy['required_metric_names']:
        try:
            rows = source_times(name)
            evidence[name] = rows
            extra['actual_source_fresh_' + name] = (
                len(rows) == 4 and {r['node'] for r in rows} == set(policy['expected_nodes'])
                and all(0 <= datetime.now(timezone.utc).timestamp() - r['source_timestamp'] <= 60 for r in rows))
        except Exception as error:
            evidence[name] = {'error':type(error).__name__ + ': ' + str(error)}
            extra['actual_source_fresh_' + name] = False
    for name, host in hosts['hosts'].items():
        clock = host.get('clock',{}).get('stdout','')
        extra[name + '_clock_synchronized'] = 'NTPSynchronized=yes' in clock
        age = (now - datetime.fromisoformat(host['at_utc'])).total_seconds()
        extra[name + '_host_snapshot_fresh'] = 0 <= age <= 180
        required = (4 if name == 'master' else 2 if name == 'router' else 8) * 1024 ** 3
        extra[name + '_memory_reserve'] = host.get('memory',{}).get('MemAvailable',0) >= required
        match=re.search(r'some\s+avg10=[\d.]+\s+avg60=([\d.]+)',host.get('pressure',{}).get('cpu',''))
        extra[name + '_cpu_pressure_safe'] = bool(match) and float(match.group(1))<=5
        extra[name + '_no_stopped_process'] = host.get('stopped_processes',{}).get('exit_code') == 0 and not host['stopped_processes']['stdout'].strip()
        active=host.get('active_competition_rehearsal_units',{}).get('stdout','')
        units=[line.strip().split()[0] for line in active.splitlines() if line.strip().split() and line.strip().split()[0].endswith('.service')]
        allowed={'ops-a03-controller.service','ops-a03-controller-rehearsal.service','ops-a03-controller-exit-test.service'} if name=='master' else set()
        if name=='master' and args.expected_observation_unit:allowed.add(args.expected_observation_unit)
        extra[name+'_no_other_competition_or_rehearsal_unit']=set(units)<=allowed
        timers=host.get('active_competition_timers',{}).get('stdout','')
        present={line.strip().split()[0] for line in timers.splitlines() if line.strip().split() and line.strip().split()[0].endswith('.timer')}
        registered=set()
        if args.expected_observation_unit and name in ('w2','w3'):
            layer='service' if name=='w3' else 'pod'
            pf=Path('/root/ops-competition-2026/attempt03-20261014/plans/scheduled-'+layer+'.json')
            sf=Path('/root/ops-competition-2026/attempt03-20261014/plans/schedule-lock.json')
            if pf.exists() and sf.exists() and read(sf).get(str(pf))==sha(pf):registered.add(read(pf)['unit']+'.timer')
        extra[name+'_no_other_competition_timer']=host.get('active_competition_timers',{}).get('exit_code')==0 and present<=registered
    spec = importlib.util.spec_from_file_location('unchanged_global_safety', SAFETY)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    global_result = module.assess(raw, policy)
    environmental = {k:v for k,v in global_result['checks'].items() if k != 'component_independent_watchdog'}
    blockers = [k for k,v in environmental.items() if not v] + [k for k,v in extra.items() if not v]
    result = {'status':'COMPETITION_ENVIRONMENT_SAFETY_FAIL' if blockers else
        'COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING',
        'at_utc':datetime.now(timezone.utc).isoformat(),
        'current_environment_blockers':blockers,
        'unchanged_global_safety_result':global_result,
        'additional_competition_environment_checks':extra,
        'actual_metric_source_timestamps':evidence,
        'research_false_flag_origin':{
            'base':'global_safety.assess computes faults_allowed from all actual safety checks',
            'wrapper':'assess_precanary_safety.main unconditionally sets faults_allowed=False and formal_fault_qualification_authorized=False after base assessment',
            'scope_only_false_can_be_reassessed':True,
            'real_environment_fail_can_be_overridden':False},
        'independent_watchdog_status':'PENDING_NEW_MECHANISM_SPECIFIC_TEST',
        'faults_allowed':False,
        'faults_injected':0,
        'research_policy_modified':False,
        'source_sha256':{str(p):sha(p) for p in (SAFETY,WRAPPER,POLICY,
            AUDIT/'global-safety-raw.json',AUDIT/'hosts.json',AUDIT/'residue.json',Path(__file__))}}
    with output.open('x') as stream: json.dump(result,stream,indent=2);stream.write('\n')
    output.chmod(0o600)
    print(json.dumps({'status':result['status'],'environment_blockers':blockers,
                     'watchdog':result['independent_watchdog_status'],'faults_allowed':False}))


if __name__ == '__main__': main()
