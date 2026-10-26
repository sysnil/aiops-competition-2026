#!/usr/bin/env python3
"""Read-only local-route and packet proof for predeclared normal controls."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


HOSTS={'k8s-worker2':('192.168.53.54','/tmp/phase2-worker2-ssh'),
       'k8s-worker3':('192.168.53.55','/tmp/phase2-worker3-ssh')}
CONTROLS=[('frontend-proxy','image-provider','k8s-worker2'),
          ('frontend','product-catalog','k8s-worker3'),
          ('checkout','currency','k8s-worker3')]


def remote(node,*cmd,timeout=100):
    ip,socket=HOSTS[node]
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,
                           'root@'+ip,*cmd],capture_output=True,text=True,timeout=timeout)


def one(spec,pods):
    source,destination,node=spec
    src=[item for item in pods if item['service']==source and item['node']==node]
    dst=[item for item in pods if item['service']==destination and item['node']==node]
    edge=source+'->'+destination
    if len(src)!=1 or len(dst)!=1:
        return {'edge':edge,'status':'UNVERIFIED_POD_PAIR_AMBIGUOUS'}
    src,dst=src[0],dst[0]
    route=remote(node,'ip','route','get',dst['ip'],timeout=10)
    if route.returncode or 'dev ens160' in route.stdout or ' dev cali' not in route.stdout:
        return {'edge':edge,'status':'UNVERIFIED_LOCAL_ROUTE','route':route.stdout.strip()}
    packet=remote(node,'timeout','80','tcpdump','-nn','-l','-s','96','-i','any','-c','1',
                  'tcp','and','src','host',src['ip'],'and','dst','host',dst['ip'],timeout=88)
    lines=[line for line in packet.stdout.splitlines() if src['ip'] in line and dst['ip'] in line]
    return {'edge':edge,'node':node,'source_pod':src,'destination_pod':dst,
            'route':route.stdout.strip(),'packet':lines[0] if lines else None,
            'packet_at_utc':datetime.now(timezone.utc).isoformat(),
            'status':'VERIFIED_LOCAL_CONTROL' if packet.returncode==0 and lines
                     else 'UNVERIFIED_NO_PACKET'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--graph',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    graph=json.loads(args.graph.read_text())
    pods=[pod for service in graph['services'] for pod in service['pods']]
    live_result=subprocess.run(['ssh','-o','BatchMode=yes',
        '-o','ControlPath=/tmp/stage4f-formal-ssh','root@192.168.53.52',
        'kubectl','-n','otel-demo','get','pods','-o','json'],
        capture_output=True,text=True,timeout=30)
    if live_result.returncode:
        raise RuntimeError('Fresh Kubernetes Pod snapshot unavailable')
    live=json.loads(live_result.stdout)
    by_uid={item['metadata']['uid']:item for item in live['items']}
    required_services={name for source,destination,_ in CONTROLS for name in (source,destination)}
    for pod in (row for row in pods if row['service'] in required_services):
        item=by_uid.get(pod['uid'])
        if not item or item['status'].get('podIP')!=pod['ip'] or item['spec'].get('nodeName')!=pod['node']:
            raise ValueError('Catalog Pod UID/IP/placement changed before control proof')
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(lambda spec:one(spec,pods),CONTROLS))
    data={'source':'public Kubernetes Pod snapshot + local route + live packet',
          'created_at_utc':datetime.now(timezone.utc).isoformat(),
          'graph_sha256':hashlib.sha256(args.graph.read_bytes()).hexdigest(),
          'fresh_pod_snapshot_sha256':hashlib.sha256(live_result.stdout.encode()).hexdigest(),
          'controls':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'verified_controls':sum(row['status']=='VERIFIED_LOCAL_CONTROL' for row in rows),
                      'attempted_controls':len(rows)}))


if __name__=='__main__':
    main()
