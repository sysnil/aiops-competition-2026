#!/usr/bin/env python3
"""Fail-closed Pod or NetworkPath preflight using current public evidence."""

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
TARGET_PAIRS={'D11':['shipping_path','payment_path'],'D12':['product_path']}


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def age(value):return (datetime.now(timezone.utc)-datetime.fromisoformat(value)).total_seconds()


def pods():
    data=json.loads(subprocess.check_output(['kubectl','-n','otel-demo','get','pods','-o','json'],
                                            text=True,timeout=30))
    return data['items']


def identity(item):
    statuses=item['status'].get('containerStatuses',[])
    if (item['metadata'].get('deletionTimestamp') or len(statuses)!=1 or
            not statuses[0].get('ready') or statuses[0].get('restartCount')!=0 or
            not statuses[0].get('containerID')):
        raise ValueError('Pod is terminating, restarting or not Ready')
    return {'service':item['metadata']['labels'].get('opentelemetry.io/name'),
            'name':item['metadata']['name'],'uid':item['metadata']['uid'],
            'pod_ip':item['status']['podIP'],'node':item['spec']['nodeName'],
            'container_id':statuses[0]['containerID']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--design',type=Path,required=True)
    parser.add_argument('--adapters',type=Path,required=True)
    parser.add_argument('--health',type=Path,required=True)
    parser.add_argument('--identity-audit',type=Path,required=True)
    parser.add_argument('--control-analysis',type=Path,required=True)
    parser.add_argument('--route-proof',type=Path)
    parser.add_argument('--experiment-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing overwrite')
    design=yaml.safe_load(args.design.read_text())
    adapters=yaml.safe_load(args.adapters.read_text())
    specs=[row for row in design['experiments'] if row['id']==args.experiment_id]
    if len(specs)!=1 or design.get('revision')!='post_rollout_actual_pod_placement_before_first_fault':
        raise ValueError('Experiment is not in the current pre-fault design revision')
    experiment=specs[0]
    health=read(args.health)
    audit=read(args.identity_audit)
    control=read(args.control_analysis)
    if (health.get('status')!='STAGE5_BASE_HEALTH_PASS' or not 0<=age(health['at_utc'])<=180 or
            audit.get('uid_coverage_fraction',0)<.99 or audit.get('uid_accuracy_fraction')!=1.0 or
            control.get('trace_ids_issued')!=control.get('trace_ids_seen')):
        raise ValueError('Health, Pod UID or control trace linkage gate failed')
    for stream in ('image_asset','product_detail','cart_view'):
        status=control['stability'][stream]
        if not status['all_five_request_bins_pass'] or not status['all_five_trace_bins_pass']:
            raise ValueError('Pre-fault fixed business stream lacks 30 requests/spans per minute')
    current=pods()
    by_uid={item['metadata']['uid']:item for item in current}
    now=datetime.now(timezone.utc)
    common={'created_at_utc':now.isoformat(),'experiment_id':args.experiment_id,
        'source_case':experiment['family'],'plan_sha256':sha(args.adapters),
        'design_sha256':sha(args.design),'health_sha256':sha(args.health),
        'identity_audit_sha256':sha(args.identity_audit),
        'control_analysis_sha256':sha(args.control_analysis),
        'faults_injected':0,'formal_case_created':False,'gt_read':False}
    if experiment['family']=='D06_TYPE_SINGLE_POD_PAUSE':
        candidates=[identity(item) for item in current if item['metadata']['labels'].get(
            'opentelemetry.io/name')=='frontend' and item['metadata'].get('deletionTimestamp') is None]
        if len(candidates)!=2 or any(item['node']!='k8s-worker2' for item in candidates):
            raise ValueError('D06 requires two current frontend Pods on Worker2')
        candidates.sort(key=lambda row:row['uid'])
        if min(len(audit['services']['frontend']['pod_stats'].get(row['uid'],{}))
               for row in candidates)==0:
            raise ValueError('A frontend replica lacks observed normal business spans')
        for minute in range(5):
            for pod in candidates:
                count=sum(row['frontend_pod_request_trace_ids'].get(pod['uid'],0)
                          for row in control['bins'] if row['minute_index']==minute and
                          row['stream'] in ('product_detail','cart_view'))
                if count<30:
                    raise ValueError('A frontend Pod lacks 30 normal control requests in a 60-second bin')
        data={**common,'status':'RH_PREFLIGHT_READY','selection_policy':
              'lexicographically_first_ready_pod_uid',
              'targets':[candidates[0]],'controls':[candidates[1]]}
    else:
        if not args.route_proof:raise ValueError('Network path needs fresh route proof')
        proof=read(args.route_proof)
        kind='D11' if experiment['family']=='D11_TYPE_NETWORK_LOSS' else 'D12'
        path=experiment['fault']['path']
        _,source,destination=path.split('/')
        if proof.get('status')!='ROUTE_FLOW_PROOF_PASS' or proof.get('mode')!=kind or (
                not 0<=age(proof['at_utc'])<=180):
            raise ValueError('Network route/flow proof failed or stale')
        targets=[row for row in proof['rows'] if row['pair'] in TARGET_PAIRS[kind]
                 and row['status']=='VERIFIED']
        controls=[row for row in proof['rows'] if row['pair'] in ('image_control','cart_control')
                  and row['status']=='VERIFIED']
        if len(targets)!=2 or len(controls)!=3:
            raise ValueError('Required two affected Pod pairs or three local controls missing')
        prefix={row['route_prefix'] for row in targets}
        if len(prefix)!=1 or None in prefix:
            raise ValueError('Affected edges lack a unique Calico route prefix')
        prefix=ipaddress.ip_network(prefix.pop())
        for row in targets+controls:
            for field in ('source_pod','destination_pod'):
                stored=row[field]
                actual=by_uid.get(stored['uid'])
                if (not actual or actual['metadata']['name']!=stored['name'] or
                        actual['status'].get('podIP')!=stored['ip'] or
                        actual['spec'].get('nodeName')!=stored['node']):
                    raise ValueError('A route/flow Pod UID, IP or placement changed')
        verified=[{'business_edge':row['edge'],'source_pod':row['source_pod']['name'],
            'source_pod_uid':row['source_pod']['uid'],
            'source_pod_ip':row['source_pod']['ip'],
            'destination_pod':row['destination_pod']['name'],
            'destination_pod_uid':row['destination_pod']['uid'],
            'destination_pod_ip':row['destination_pod']['ip'],
            'route_text':row['route'],'flow_proof':row['packet']}
            for row in targets]
        if not all(ipaddress.ip_address(row['destination_pod_ip']) in prefix for row in verified):
            raise ValueError('Destination Pod outside verified route prefix')
        data={**common,'status':'RH_NETWORK_PREFLIGHT_READY',
            'path':path,'source_node':source,'destination_node':destination,
            'source_node_ip':NODES[source],'destination_node_ip':NODES[destination],
            'business_route_prefix':str(prefix),
            'verified_traversing_edges':verified,
            'qualified_local_controls':[{'edge':row['edge'],
                'source_uid':row['source_pod']['uid'],'destination_uid':row['destination_pod']['uid'],
                'packet':row['packet']} for row in controls],
            'route_proof_sha256':sha(args.route_proof)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'status':data['status'],'id':args.experiment_id,
        'target_uids':[row['uid'] for row in data['targets']] if 'targets' in data else
                     [row['destination_pod_uid'] for row in data['verified_traversing_edges']]}))


if __name__=='__main__':main()
