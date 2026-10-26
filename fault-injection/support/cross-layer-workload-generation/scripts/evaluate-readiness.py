#!/usr/bin/env python3
"""Proposed pre-fault workload gate; never reads case GT or predictions."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics

import yaml


def read(path):
    return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--health',type=Path,required=True)
    parser.add_argument('--route-proof',type=Path,required=True)
    parser.add_argument('--control-proof',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    rules=yaml.safe_load((args.root/'design/workload-readiness.yaml').read_text())
    manifest=read(args.root/'runs'/args.run/'result-manifest.json')
    traces=read(args.root/'observations'/f'{args.run}-trace-coverage.json')
    phases=read(args.root/'observations'/f'{args.run}-phase-coverage.json')['phases']
    metrics=read(args.root/'observations'/f'{args.run}-resource-curves/resource-metrics.json')
    health=read(args.health)
    route=read(args.route_proof)
    controls=read(args.control_proof)
    gates={}
    gates['clean_workload']=manifest['status']=='LOAD_PASS' and manifest['session_success_fraction']>=rules['minimum_success_fraction']
    health_age=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at_utc'])).total_seconds()
    gates['health']=health['status']=='STAGE5_BASE_HEALTH_PASS' and len(health['nodes_ready'])==4 and 0<=health_age<=180
    gates['trace_linkage']=traces['matched_trace_ids']==traces['query_trace_ids']
    gates['service_frontend']=traces['services'].get('frontend',0)>=rules['minimum_target_service_spans']
    gates['service_ad']=traces['services'].get('ad',0)>=rules['minimum_target_service_spans']
    gates['pod_request_identity']=traces['pod_identified_span_fraction']>0
    route_age=(datetime.now(timezone.utc)-datetime.fromisoformat(route['created_at_utc'])).total_seconds()
    gates['verified_networkpaths']=0<=route_age<=180 and len(route['verified_paths'])>=3 and all(
        row['status']=='VERIFIED' for row in route['results'])
    control_age=(datetime.now(timezone.utc)-datetime.fromisoformat(controls['created_at_utc'])).total_seconds()
    gates['independent_local_controls']=0<=control_age<=180 and all(row['status']=='VERIFIED_LOCAL_CONTROL'
                                            for row in controls['controls'])
    rule_plan=yaml.safe_load((args.root/'design/fault-adaptation-plan.yaml').read_text())
    control_edges=sorted({edge for values in rule_plan['controls']['selected_before_fault'].values()
                          for edge in values})
    graph=read(args.root/'catalog/service-call-graph.json')
    replica_count={service['name']:len(service['pods']) for service in graph['services']}
    path_control_identity={path:all(replica_count.get(edge.split('->')[0],0)==1 or
        gates['pod_request_identity'] for edge in edges)
        for path,edges in rule_plan['controls']['selected_before_fault'].items()}
    control_counts={edge:{phase:row['edges'].get(edge,0) for phase,row in phases.items()}
                    for edge in control_edges}
    gates['control_samples']=all(min(counts.values())>=rules['minimum_control_edge_spans']
                                 for counts in control_counts.values())
    gates['control_identity_all_paths']=all(path_control_identity.values())
    ad_cpu={}
    for item in metrics['queries']['pod_cpu_cores']['series']:
        if item['metric'].get('container')!='ad': continue
        vals=[float(value) for _,value in item['values']]
        ad_cpu[item['metric']['pod']]={'median_cores':statistics.median(vals),
                                       'max_cores':max(vals)}
    gates['ad_quota_demand']=bool(ad_cpu) and all(row['median_cores']>=.25 for row in ad_cpu.values())
    decision={'SERVICE':'READY_FOR_BOUNDED_REHEARSAL' if all(gates[key] for key in
        ('clean_workload','health','trace_linkage','service_frontend')) else 'BLOCKED',
        'POD_CPU_QUOTA':'READY_FOR_BOUNDED_REHEARSAL' if all(gates[key] for key in
        ('clean_workload','health','service_ad','ad_quota_demand')) else 'BLOCKED_LOW_CPU_DEMAND',
        'POD_IDENTITY':'READY_FOR_FORMAL_CANDIDATE' if gates['pod_request_identity'] else 'BLOCKED_NO_POD_REQUEST_IDENTITY',
        'NETWORK_PATH':'READY_FOR_BOUNDED_REHEARSAL' if all(gates[key] for key in
        ('clean_workload','health','verified_networkpaths','independent_local_controls',
         'control_samples','control_identity_all_paths')) else 'BLOCKED_CONTROL_IDENTITY_OR_OTHER_GATE'}
    path_decisions={path:('READY_FOR_BOUNDED_REHEARSAL' if identity and
        gates['clean_workload'] and gates['health'] and gates['verified_networkpaths'] and
        gates['independent_local_controls'] and all(min(control_counts[edge].values())>=
        rules['minimum_control_edge_spans'] for edge in edges)
        else 'BLOCKED') for path,identity in path_control_identity.items()
        for edges in [rule_plan['controls']['selected_before_fault'][path]]}
    result={'decision':decision,'path_decisions':path_decisions,
            'path_control_identity':path_control_identity,'gates':gates,
            'control_span_counts_per_60s':control_counts,
            'ad_cpu_demand':ad_cpu,'pod_identified_span_fraction':traces['pod_identified_span_fraction'],
            'run':args.run,'health_age_seconds':health_age,'route_age_seconds':route_age,
            'control_age_seconds':control_age,'future_protocol_frozen':False,'gt_accessed':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'decision':decision,'gates':gates}))


if __name__=='__main__':
    main()
