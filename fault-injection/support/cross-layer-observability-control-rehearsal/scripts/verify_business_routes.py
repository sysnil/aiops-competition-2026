#!/usr/bin/env python3
"""Fresh Pod UID, Node route and payload-bearing business packet proof."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess


MASTER=('192.168.53.52','/tmp/stage4f-formal-ssh')
HOSTS={'k8s-worker1':('192.168.53.53','/tmp/oc-worker1-ssh'),
       'k8s-worker2':('192.168.53.54','/tmp/oc-worker2-ssh'),
       'k8s-worker3':('192.168.53.55','/tmp/oc-worker3-ssh')}
PAIRS={
    'image_control':('frontend-proxy','k8s-worker2','image-provider','k8s-worker2',8081),
    'cart_control':('frontend','k8s-worker2','cart','k8s-worker2',8080),
    'product_path':('frontend','k8s-worker2','product-catalog','k8s-worker3',8080),
    'shipping_path':('checkout','k8s-worker3','shipping','k8s-worker1',8080),
    'payment_path':('checkout','k8s-worker3','payment','k8s-worker1',8080),
    'currency_path':('frontend','k8s-worker2','currency','k8s-worker3',8080)}
MODES={'clean':{'image_control','cart_control','product_path','shipping_path'},
       'D06':{'image_control','cart_control','product_path','shipping_path'},
       'D11':{'shipping_path','payment_path','image_control','cart_control','product_path'},
       'D12':{'product_path','image_control','cart_control'}}


def remote(host,*args,timeout=100):
    ip,socket=MASTER if host=='master' else HOSTS[host]
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ControlPath='+socket,
                           'root@'+ip,*args],capture_output=True,text=True,timeout=timeout)


def observe(name,source,destination,src_node,dst_node,dst_port):
    route=remote(src_node,'ip','route','get',destination['ip'],timeout=10)
    if route.returncode:
        return {'pair':name,'status':'ROUTE_QUERY_FAILED','stderr':route.stderr[-300:]}
    same=src_node==dst_node
    if same:
        route_ok=' dev cali' in route.stdout and ' dev ens160' not in route.stdout
        prefix=None
    else:
        dst_node_ip=HOSTS[dst_node][0]
        route_ok=('via '+dst_node_ip in route.stdout and ' dev ens160 ' in route.stdout)
        table=remote(src_node,'ip','route','show',timeout=10)
        matching=[]
        for line in table.stdout.splitlines():
            parts=line.split()
            if (parts and '/' in parts[0] and 'via '+dst_node_ip in line and
                    ' dev ens160' in line and ipaddress.ip_address(destination['ip']) in
                    ipaddress.ip_network(parts[0])):
                matching.append(parts[0])
        prefix=matching[0] if len(matching)==1 else None
        route_ok=route_ok and prefix is not None
    if not route_ok:
        return {'pair':name,'status':'ROUTE_UNVERIFIED','route':route.stdout.strip()}
    interface='any' if same else 'ens160'
    capture=remote(src_node,'timeout','85','tcpdump','-nn','-l','-s','96',
        '-i',interface,'-c','5','tcp','and','src','host',source['ip'],
        'and','dst','host',destination['ip'],'and','dst','port',str(dst_port),timeout=95)
    payload=[line for line in capture.stdout.splitlines() if source['ip'] in line and
             destination['ip'] in line and (match:=re.search(r'length (\d+)',line)) and
             int(match.group(1))>0]
    return {'pair':name,'edge':source['service']+'->'+destination['service'],
        'status':'VERIFIED' if payload else 'NO_PAYLOAD_PACKET',
        'captured_at_utc':datetime.now(timezone.utc).isoformat(),
        'source_pod':source,'destination_pod':destination,
        'route':route.stdout.strip(),'route_prefix':prefix,
        'source_interface':interface,'packet':payload[0] if payload else None,
        'packet_output_sha256':hashlib.sha256(capture.stdout.encode()).hexdigest(),
        'capture_exit_code':capture.returncode,
        'packet_count_observed':len(payload)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=MODES,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing overwrite')
    live=remote('master','kubectl','-n','otel-demo','get','pods','-o','json',timeout=30)
    if live.returncode:raise RuntimeError('Fresh Kubernetes Pod snapshot failed')
    pods=[]
    for item in json.loads(live.stdout)['items']:
        if item['metadata'].get('deletionTimestamp') is not None:continue
        if not all(status.get('ready') for status in item['status'].get('containerStatuses',[])):
            continue
        pods.append({'service':item['metadata']['labels'].get('opentelemetry.io/name'),
            'name':item['metadata']['name'],'uid':item['metadata']['uid'],
            'ip':item['status'].get('podIP'),'node':item['spec'].get('nodeName')})
    work=[]
    for name in sorted(MODES[args.mode]):
        src_service,src_node,dst_service,dst_node,port=PAIRS[name]
        sources=[row for row in pods if row['service']==src_service and row['node']==src_node]
        destinations=[row for row in pods if row['service']==dst_service and row['node']==dst_node]
        if name in ('cart_control','product_path','currency_path'):
            if len(sources)!=2:raise ValueError(name+' requires two identified frontend Pods on Worker2')
        elif len(sources)!=1:
            raise ValueError(name+' source Pod identity is not unique')
        if len(destinations)!=1:
            raise ValueError(name+' destination Pod identity is not unique')
        for source in sources:
            work.append((name,source,destinations[0],src_node,dst_node,port))
    with ThreadPoolExecutor(max_workers=len(work)) as pool:
        rows=list(pool.map(lambda item:observe(*item),work))
    result={'status':'ROUTE_FLOW_PROOF_PASS' if all(row['status']=='VERIFIED' for row in rows)
            else 'ROUTE_FLOW_PROOF_INCOMPLETE',
            'mode':args.mode,'at_utc':datetime.now(timezone.utc).isoformat(),
            'pod_snapshot_sha256':hashlib.sha256(live.stdout.encode()).hexdigest(),
            'rows':rows,'no_fault_injected':True}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'verified':sum(row['status']=='VERIFIED'
        for row in rows),'expected':len(rows)}))
    if result['status']!='ROUTE_FLOW_PROOF_PASS':raise SystemExit(1)


if __name__=='__main__':main()
