#!/usr/bin/env python3
"""Fresh five-host collection followed immediately by unchanged base safety checks."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import shlex
import subprocess
import sys

from master_host_audit import HOSTS,one

LOCAL=Path(__file__).resolve().parents[2]
DATA='/data/stage5-v3/ops-competition-2026/audits/'

def remote(argv):
    r=subprocess.run(['ssh','-o','BatchMode=yes','-S','/run/ops-a02-ssh/master.sock',
        'root@192.168.53.52',shlex.join(argv)],capture_output=True,text=True,timeout=90)
    if r.returncode:raise RuntimeError(r.stderr[-1000:])
    return r.stdout

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--id',required=True)
    parser.add_argument('--expected-observation-unit');args=parser.parse_args()
    if not args.id.startswith('COMP-SF01-') or not args.id.replace('-','').isalnum():raise RuntimeError('Invalid safety audit ID')
    out=LOCAL/'qualification'/args.id
    if out.exists():raise RuntimeError('Audit already exists')
    out.mkdir(parents=True,mode=0o700)
    with ThreadPoolExecutor(max_workers=5) as pool:rows=dict(pool.map(one,HOSTS.items()))
    hosts={'at_utc':datetime.now(timezone.utc).isoformat(),'hosts':rows,
           'scope':'READ_ONLY_COMPETITION_PHASE_A','faults_injected':0}
    with (out/'hosts.json').open('x') as f:json.dump(hosts,f,indent=2);f.write('\n')
    cmd=[sys.executable,'-B',str(LOCAL.parent/'stage5-v3-autonomous-preparation/scripts/audit_hosts.py')]
    names={'master':'k8s-master','w1':'k8s-worker1','w2':'k8s-worker2','w3':'k8s-worker3','router':'router'}
    for n,ip in HOSTS.items():cmd+=['--host',','.join((names[n],ip,'/run/ops-a02-ssh/'+n+'.sock'))]
    cmd+=['--output',str(out/'residue.json')]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError('Five-host residue failed: '+result.stdout+result.stderr[-500:])
    path=DATA+args.id
    remote(['python3','-c',
        'import sys,pathlib;sys.path.insert(0,"/root/ops-competition-2026/scripts");'
        'from competition_observation import storage;storage();'
        'p=pathlib.Path('+repr(path)+');assert not p.exists();p.mkdir(mode=0o700)'])
    subprocess.run(['scp','-p','-o','BatchMode=yes','-o','ControlPath=/run/ops-a02-ssh/master.sock',
        str(out/'hosts.json'),str(out/'residue.json'),'root@192.168.53.52:'+path+'/'],check=True,timeout=30)
    command=['env','KUBECONFIG=/etc/kubernetes/admin.conf','python3','-B',
        '/root/ops-competition-2026/attempt02-20261014/scripts/attempt_preflight.py','--audit-dir',path,'--refresh']
    if args.expected_observation_unit:command+=['--expected-observation-unit',args.expected_observation_unit]
    result=remote(command)
    subprocess.run(['scp','-p','-o','BatchMode=yes','-o','ControlPath=/run/ops-a02-ssh/master.sock',
        'root@192.168.53.52:'+path+'/phase-a-decision.json',str(out/'phase-a-decision.json')],check=True,timeout=30)
    for p in out.iterdir():p.chmod(0o600)
    print(result.strip())
    decision=json.loads((out/'phase-a-decision.json').read_text())
    if decision['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING':raise SystemExit(1)

if __name__=='__main__':main()
