#!/usr/bin/env python3
"""Public baseline identity/traffic precheck before a planned competition fault."""

import argparse
from datetime import datetime,timezone,timedelta
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

from competition_observation import ROOT,DATA,OLD,CG,save,sha
from competition_qualification import span_error

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True)
    args=parser.parse_args();plan=json.loads(args.plan.read_text())
    run=DATA/'runs/SF-PILOT-01';schedule=json.loads((run/'schedule.json').read_text())
    baseline_end=datetime.fromisoformat(schedule['private_fault_schedule'][plan['id']]['start_utc'])
    start=baseline_end-timedelta(seconds=120);end=baseline_end-timedelta(seconds=60)
    if time.time()<end.timestamp() or time.time()-end.timestamp()>120:raise RuntimeError('Baseline prefix query is not live')
    out=run/('baseline-prefix-'+plan['layer'].lower())
    cmd=['/usr/bin/python3','-B',str(OLD/'capture_live_c2.py'),'--phase','before',
        '--start-utc',start.isoformat(),'--end-utc',end.isoformat(),'--trace-source',
        'JAEGER_OPENSEARCH_STORAGE','--output',str(out)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=65)
    if r.returncode:raise RuntimeError('Baseline prefix capture failed: '+r.stderr[-500:])
    sys.path.insert(0,str(CG));from trace_identity import span_map
    spans=[v[1] for v in span_map(out).values()]
    counts={t['uid']:set() for t in plan['targets_and_siblings']}
    errors=[]
    for s in spans:
        if (s.get('tag') or {}).get('span@kind')!='server':continue
        uid={r.get('key'):r.get('value') for r in s['process'].get('tags') or []}.get('k8s.pod.uid')
        if uid in counts:counts[uid].add(s['traceID'])
        if s['process']['serviceName']=='frontend-proxy' and span_error(s):errors.append(s['traceID'])
    service=plan['targets'][0]['service']
    relevant=[t for t in plan['targets_and_siblings'] if t['service']==service]
    checks={'no_public_ingress_error_span':not errors,
            'target_and_sibling_business_coverage':all(len(counts[t['uid']])>=10 for t in relevant),
            'all_three_capture_paths':(out/'metrics/memory.json').is_file() and
                json.loads((out/'capture-manifest.json').read_text())['log_records']>0}
    result={'status':'COMPETITION_BASELINE_PREFIX_PASS' if all(checks.values()) else 'COMPETITION_BASELINE_PREFIX_FAIL',
        'id':plan['id'],'at_utc':datetime.now(timezone.utc).isoformat(),'checks':checks,
        'per_uid_server_trace_count':{k:len(v) for k,v in counts.items()},
        'public_ingress_error_trace_ids':sorted(set(errors)),
        'capture_manifest_sha256':sha(out/'capture-manifest.json'),'faults_injected':0}
    save(run/('baseline-prefix-'+plan['layer'].lower()+'-decision.json'),result);print(json.dumps(result))
    if result['status']!='COMPETITION_BASELINE_PREFIX_PASS':raise SystemExit(1)

if __name__=='__main__':main()
