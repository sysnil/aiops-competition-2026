#!/usr/bin/env python3
"""Read-only, fresh route plus interface packet proof for business edges."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess


HOSTS = {'k8s-worker1': ('192.168.53.53', '/tmp/phase2-worker1-ssh'),
         'k8s-worker2': ('192.168.53.54', '/tmp/phase2-worker2-ssh'),
         'k8s-worker3': ('192.168.53.55', '/tmp/phase2-worker3-ssh')}
EDGES = [
    ('frontend', 'k8s-worker3', 'cart', 'k8s-worker2'),
    ('checkout', 'k8s-worker3', 'cart', 'k8s-worker2'),
    ('frontend', 'k8s-worker3', 'shipping', 'k8s-worker1'),
    ('checkout', 'k8s-worker3', 'shipping', 'k8s-worker1'),
    ('frontend-proxy', 'k8s-worker2', 'frontend', 'k8s-worker3'),
    ('frontend', 'k8s-worker2', 'currency', 'k8s-worker3'),
]


def remote(node, *command, timeout=100):
    ip, socket = HOSTS[node]
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ControlPath='+socket,
                             'root@'+ip, *command], capture_output=True,
                            text=True, timeout=timeout)
    return result


def one(edge, pods):
    source, source_node, destination, destination_node = edge
    candidates = [row for row in pods if row['service']==source and row['node']==source_node]
    targets = [row for row in pods if row['service']==destination and row['node']==destination_node]
    if len(candidates)!=1 or len(targets)!=1:
        return {'edge': source+'->'+destination, 'status':'UNVERIFIED_POD_PAIR_AMBIGUOUS'}
    src, dst = candidates[0], targets[0]
    route = remote(source_node,'ip','route','get',dst['ip'],timeout=10)
    if (route.returncode or 'dev ens160' not in route.stdout or
            ('via '+HOSTS[destination_node][0]) not in route.stdout):
        return {'edge':source+'->'+destination,'status':'UNVERIFIED_ROUTE',
                'route':route.stdout.strip(),'route_stderr':route.stderr.strip()}
    table=remote(source_node,'ip','route','show',timeout=10)
    matching=[]
    for line in table.stdout.splitlines():
        parts=line.split()
        if (parts and '/' in parts[0] and
                'via '+HOSTS[destination_node][0] in line and ' dev ens160' in line and
                ipaddress.ip_address(dst['ip']) in ipaddress.ip_network(parts[0])):
            matching.append(parts[0])
    if table.returncode or len(matching)!=1:
        return {'edge':source+'->'+destination,'status':'UNVERIFIED_ROUTE_PREFIX',
                'route':route.stdout.strip()}
    cmd = ['timeout','80','tcpdump','-nn','-l','-s','96','-i','ens160','-c','1',
           'tcp','and','src','host',src['ip'],'and','dst','host',dst['ip']]
    packet=remote(source_node,*cmd,timeout=88)
    lines=[line for line in packet.stdout.splitlines() if src['ip'] in line and dst['ip'] in line]
    return {'edge':source+'->'+destination,
            'network_path':f'NETWORK_PATH/{source_node}/{destination_node}',
            'source_pod':src,'destination_pod':dst,
            'route':route.stdout.strip(),'route_sha256':hashlib.sha256(route.stdout.encode()).hexdigest(),
            'business_route_prefix':matching[0],
            'packet_observed_at_utc':datetime.now(timezone.utc).isoformat(),
            'packet':lines[0] if lines else None,
            'packet_sha256':hashlib.sha256(packet.stdout.encode()).hexdigest(),
            'capture_exit_code':packet.returncode,
            'status':'VERIFIED' if packet.returncode==0 and lines else 'UNVERIFIED_NO_PACKET'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--graph',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite route proof')
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
    required_services={name for source,_,destination,_ in EDGES for name in (source,destination)}
    for pod in (row for row in pods if row['service'] in required_services):
        item=by_uid.get(pod['uid'])
        if not item or item['status'].get('podIP')!=pod['ip'] or item['spec'].get('nodeName')!=pod['node']:
            raise ValueError('Catalog Pod UID/IP/placement changed before flow proof')
    with ThreadPoolExecutor(max_workers=len(EDGES)) as pool:
        results=list(pool.map(lambda edge:one(edge,pods),EDGES))
    data={'source':'public Kubernetes Pod snapshot + Node route + ens160 packet',
          'created_at_utc':datetime.now(timezone.utc).isoformat(),
          'graph_sha256':hashlib.sha256(args.graph.read_bytes()).hexdigest(),
          'fresh_pod_snapshot_sha256':hashlib.sha256(live_result.stdout.encode()).hexdigest(),
          'results':results,
          'verified_paths':sorted({row['network_path'] for row in results
                                   if row['status']=='VERIFIED'})}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'verified_edges':sum(row['status']=='VERIFIED' for row in results),
                      'attempted_edges':len(results),'verified_paths':data['verified_paths']}))


if __name__=='__main__':
    main()
