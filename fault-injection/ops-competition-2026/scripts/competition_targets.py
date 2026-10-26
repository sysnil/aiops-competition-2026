#!/usr/bin/env python3
"""Inspect current contest candidate Pod/container/cgroup identities without mutation."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess

HOSTS={'master':'192.168.53.52','w1':'192.168.53.53','w2':'192.168.53.54','w3':'192.168.53.55'}
NODES={'k8s-worker1':'w1','k8s-worker2':'w2','k8s-worker3':'w3'}

def remote(host,argv):
    r=subprocess.run(['ssh','-o','BatchMode=yes','-S','/tmp/sfgen-'+host+'.sock',
        'root@'+HOSTS[host],shlex.join(argv)],capture_output=True,text=True,timeout=40)
    if r.returncode: raise RuntimeError(host+': '+r.stderr[-500:])
    return r.stdout

CODE=r'''
import json,pathlib,subprocess,sys,socket
t=json.loads(sys.argv[1]); bare=t['container_id'].removeprefix('containerd://')
rows=[l.split() for l in subprocess.check_output(['ctr','-n','k8s.io','tasks','list'],text=True).splitlines()]
hits=[int(r[1]) for r in rows if len(r)>=3 and r[0]==bare and r[2]=='RUNNING']
assert len(hits)==1
pid=hits[0]; rel=pathlib.Path('/proc',str(pid),'cgroup').read_text().strip().split('::',1)[1]
assert t['uid'].replace('-','_') in rel and bare in rel
cg=pathlib.Path('/sys/fs/cgroup')/rel.lstrip('/')
limit=(cg/'memory.max').read_text().strip(); current=int((cg/'memory.current').read_text())
assert limit!='max'
procs=[int(p) for p in (cg/'cgroup.procs').read_text().split()]
processes=[]
for p in procs:
 try:
  stat=pathlib.Path('/proc',str(p),'stat').read_text().rsplit(')',1)[1].split()
  processes.append({'pid':p,'start_ticks':stat[19],'state':stat[0],
    'comm':pathlib.Path('/proc',str(p),'comm').read_text().strip()})
 except FileNotFoundError:pass
print(json.dumps({**t,'host':socket.gethostname(),'container_task_pid':pid,
 'cgroup':str(cg),'processes':processes,'memory_current_bytes':current,
 'memory_max_bytes':int(limit),'memory_events':(cg/'memory.events').read_text(),
 'cpu_max':(cg/'cpu.max').read_text().strip(),
 'old_adapter_headroom_bytes':min(int((int(limit)-current)*.55),int(int(limit)*.8)-current),
 'conservative_32MiB_plus_32MiB_overhead_fits_80pct':current+64*1024**2<=int(limit)*.8}))
'''

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise RuntimeError('New inspection output required')
    k=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','-n','otel-demo']
    pods=json.loads(remote('master',k+['get','pods','-o','json']))['items']
    result=[]
    for p in pods:
        service=p['metadata']['labels'].get('opentelemetry.io/name')
        if service not in ('recommendation','frontend'):continue
        cs=p['status'].get('containerStatuses',[])
        if len(cs)!=1 or not cs[0].get('ready') or p['status'].get('phase')!='Running':
            raise RuntimeError('Candidate container is not uniquely Ready')
        t={'service':service,'name':p['metadata']['name'],'uid':p['metadata']['uid'],
           'node':p['spec']['nodeName'],'pod_ip':p['status']['podIP'],
           'container_id':cs[0]['containerID'],'restart_count':cs[0]['restartCount']}
        result.append(json.loads(remote(NODES[t['node']],['python3','-c',CODE,json.dumps(t)])))
    value={'status':'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED',
        'at_utc':datetime.now(timezone.utc).isoformat(),'targets':result,'faults_injected':0}
    args.output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with args.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({'status':value['status'],'targets':[{'name':r['name'],
        'memory_current':r['memory_current_bytes'],'memory_limit':r['memory_max_bytes'],
        'small_allocation_safe':r['conservative_32MiB_plus_32MiB_overhead_fits_80pct'],
        'processes':r['processes']} for r in result]}))

if __name__=='__main__':main()
