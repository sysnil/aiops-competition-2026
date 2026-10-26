#!/usr/bin/env python3
"""Bind fresh public route/packet proof and Pod identities before network rehearsal."""

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess

import yaml


NODES={'k8s-worker1':'192.168.53.53','k8s-worker2':'192.168.53.54',
       'k8s-worker3':'192.168.53.55'}


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--health',type=Path,required=True)
    parser.add_argument('--route-proof',type=Path,required=True)
    parser.add_argument('--control-proof',type=Path,required=True)
    parser.add_argument('--experiment-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    plan=yaml.safe_load(args.plan.read_text())
    matches=[row for row in plan['network_experiments'] if row['id']==args.experiment_id]
    if len(matches)!=1: raise ValueError('Experiment not predeclared')
    spec=matches[0]
    health=read(args.health)
    route=read(args.route_proof)
    controls=read(args.control_proof)
    now=datetime.now(timezone.utc)
    ages=[(now-datetime.fromisoformat(value)).total_seconds() for value in
          (health['at_utc'],route['created_at_utc'],controls['created_at_utc'])]
    if (health['status']!='STAGE5_BASE_HEALTH_PASS' or
            any(not 0<=age<=180 for age in ages)):
        raise ValueError('Health/route/control evidence is stale or failed')
    path=spec['path']
    _,source,destination=path.split('/')
    pairs=[row for row in route['results'] if row.get('network_path')==path and
           row['status']=='VERIFIED']
    if len(pairs)<2:
        raise ValueError('Fewer than two verified business edges on path')
    selected=plan['controls']['selected_before_fault'][path]
    qualified=[row for row in controls['controls'] if row['edge'] in selected and
               row['status']=='VERIFIED_LOCAL_CONTROL']
    if len(qualified)!=len(selected):
        raise ValueError('Predeclared independent control lacks local flow proof')
    live=json.loads(subprocess.check_output(['kubectl','-n','otel-demo','get','pods','-o','json'],
                                            text=True,timeout=30))
    by_uid={row['metadata']['uid']:row for row in live['items']}
    verified=[]
    for row in pairs:
        src,dst=row['source_pod'],row['destination_pod']
        for pod in (src,dst):
            live_pod=by_uid.get(pod['uid'])
            if (not live_pod or live_pod['status'].get('podIP')!=pod['ip'] or
                    live_pod['spec'].get('nodeName')!=pod['node']):
                raise ValueError('A route proof Pod UID/IP/placement changed')
        if (src['node']!=source or dst['node']!=destination or
                'via '+NODES[destination] not in row['route'] or
                ' dev ens160 ' not in row['route']):
            raise ValueError('A verified flow no longer matches the network path')
        verified.append({'business_edge':row['edge'],'source_pod':src['pod'],
                         'source_pod_uid':src['uid'],'source_pod_ip':src['ip'],
                         'destination_pod':dst['pod'],'destination_pod_uid':dst['uid'],
                         'destination_pod_ip':dst['ip'],
                         'route_text':row['route'],'flow_proof':row['packet']})
    prefixes={row['business_route_prefix'] for row in pairs}
    if len(prefixes)!=1:
        raise ValueError('Verified edges do not share a unique Calico route prefix')
    prefix=ipaddress.ip_network(prefixes.pop())
    if not all(ipaddress.ip_address(row['destination_pod_ip']) in prefix for row in verified):
        raise ValueError('Destination Pods not in current Node Pod CIDR')
    data={'status':'RH_NETWORK_PREFLIGHT_READY','created_at_utc':now.isoformat(),
          'experiment_id':args.experiment_id,'source_case':spec['source_case'],
          'path':path,'source_node':source,'destination_node':destination,
          'source_node_ip':NODES[source],'destination_node_ip':NODES[destination],
          'business_route_prefix':str(prefix),'verified_traversing_edges':verified,
          'qualified_local_controls':qualified,'plan_sha256':sha(args.plan),
          'health_sha256':sha(args.health),'route_proof_sha256':sha(args.route_proof),
          'control_proof_sha256':sha(args.control_proof),
          'faults_injected':0,'formal_case_created':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'status':data['status'],'path':path,
                      'verified_edges':len(verified),'qualified_controls':len(qualified)}))


if __name__=='__main__':
    main()
