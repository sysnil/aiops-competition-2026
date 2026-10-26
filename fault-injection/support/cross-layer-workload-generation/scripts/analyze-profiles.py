#!/usr/bin/env python3
"""Compare observed workload runs against the pre-run business call graph."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


def read(path):
    return json.loads(path.read_text())


def safe_write(fig, path):
    fig.update_layout(template='plotly_white', font=dict(family='Arial'))
    fig.write_html(path, include_plotlyjs=True, full_html=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    root = args.root
    graph = read(root/'catalog/service-call-graph.json')
    service_universe = set(graph['business_service_denominator'])
    edge_universe = set(graph['business_edge_denominator'])
    plots = root/'figures'
    plots.mkdir(exist_ok=True)
    rows, service_rows, workflow_rows, pod_resource_rows = [], [], [], []
    run_dirs = sorted(path for path in (root/'runs').iterdir()
                      if (path/'result-manifest.json').exists() and 'smoke' not in path.name)
    proof_files=list((root/'observations').glob('route-proof*.json'))
    for path in run_dirs:
        manifest = read(path/'result-manifest.json')
        trace_path = root/'observations'/f'{path.name}-trace-coverage.json'
        if not trace_path.exists():
            continue
        traces = read(trace_path)
        observed_services = set(traces['services']) & service_universe
        observed_edges = set(traces['edges']) & edge_universe
        sessions = [json.loads(line) for line in (path/'sessions.jsonl').read_text().splitlines()]
        workflows = Counter(row['workflow'] for row in sessions if row['completed'])
        branches = Counter(row.get('branch') for row in sessions if row['completed'])
        start=datetime.fromisoformat(manifest['started_utc'])
        end=datetime.fromisoformat(manifest['ended_utc'])
        run_proofs=[]
        for proof_file in proof_files:
            proof=read(proof_file)
            for item in proof['results']:
                at=item.get('packet_observed_at_utc')
                if item['status']=='VERIFIED' and at and start<=datetime.fromisoformat(at)<=end:
                    run_proofs.append(item)
        verified_paths=sorted({item['network_path'] for item in run_proofs})
        row = {'run': path.name, 'profile': manifest['profile'], 'seed': manifest['seed'],
               'sessions': manifest['completed_sessions'],
               'success_fraction': manifest['session_success_fraction'],
               'request_qps': manifest['request_qps'],
               'p50_ms': manifest['latency_ms']['p50'],
               'p95_ms': manifest['latency_ms']['p95'],
               'p99_ms': manifest['latency_ms']['p99'],
               'service_coverage': len(observed_services)/len(service_universe),
               'service_covered': len(observed_services),
               'service_denominator': len(service_universe),
               'edge_coverage': len(observed_edges)/len(edge_universe),
               'edge_covered': len(observed_edges),
               'edge_denominator': len(edge_universe),
               'workflow_types_completed': len(workflows),
               'workflow_branches_completed': len(branches),
               'matched_trace_ids': traces['matched_trace_ids'],
               'query_trace_ids': traces['query_trace_ids'],
               'pod_coverage': None,
               'pod_coverage_reason': '0 percent of Jaeger spans identify a Kubernetes Pod',
               'networkpath_coverage': len(verified_paths)/3 if run_proofs else None,
               'networkpath_coverage_reason': 'fresh route and packet proof; per-path call count is a lower bound' if run_proofs else 'no simultaneous route and packet-flow proof',
               'verified_networkpaths': verified_paths,
               'verified_business_flow_packet_events': len(run_proofs),
               'missing_services': sorted(service_universe-observed_services),
               'missing_edges': sorted(edge_universe-observed_edges),
               'status': manifest['status']}
        rows.append(row)
        for service in sorted(service_universe):
            service_rows.append({'run': path.name, 'profile': manifest['profile'],
                                 'service': service, 'spans': traces['services'].get(service, 0)})
        for workflow, count in workflows.items():
            workflow_rows.append({'run': path.name, 'profile': manifest['profile'],
                                  'workflow': workflow, 'completed': count})
        resource_path=root/'observations'/f'{path.name}-resource-curves/resource-metrics.json'
        if resource_path.exists():
            resource=read(resource_path)
            for item in resource['queries']['pod_cpu_cores']['series']:
                values=sorted(float(value) for _,value in item['values'])
                if values:
                    pod_resource_rows.append({'run':path.name,
                        'pod':item['metric'].get('pod','unknown'),
                        'median_cpu_cores':values[len(values)//2]})
    if not rows:
        raise ValueError('No completed runs with trace coverage')
    summary = {'source': 'generated request logs joined by traceID to Jaeger OpenSearch',
               'denominator_sha256': graph['baseline_sha256'],
               'runs': rows}
    (root/'observations/profile-analysis.json').write_text(json.dumps(summary, indent=2)+'\n')
    pd.DataFrame([{k:v for k,v in row.items() if not isinstance(v,list)} for row in rows]).to_csv(
        root/'observations/profile-analysis.csv',index=False)

    business_edges = [edge for edge in graph['edges'] if edge['category']=='BUSINESS_REACHABLE']
    network = nx.DiGraph()
    for edge in business_edges:
        network.add_edge(edge['source'],edge['destination'],weight=edge['baseline_span_count'])
    coords = nx.spring_layout(network,seed=101,weight='weight')
    fig = go.Figure()
    for src,dst,data in network.edges(data=True):
        a,b=coords[src],coords[dst]
        fig.add_trace(go.Scatter(x=[a[0],b[0]],y=[a[1],b[1]],mode='lines',
            line=dict(width=1.5,color='#9aa4ab'),showlegend=False,
            hovertext=f'{src} -> {dst}: {data["weight"]} baseline spans',hoverinfo='text'))
    fig.add_trace(go.Scatter(x=[coords[n][0] for n in network],
        y=[coords[n][1] for n in network],mode='markers+text',text=list(network),
        textposition='top center',marker=dict(size=16,color='#1e7a78'),
        hoverinfo='text',showlegend=False))
    fig.update_layout(title='Observed business service call graph',height=700,
        xaxis=dict(visible=False),yaxis=dict(visible=False),margin=dict(l=30,r=30,t=60,b=30))
    safe_write(fig,plots/'service-call-topology.html')

    fig=px.bar(pd.DataFrame(service_rows),x='service',y='spans',color='profile',
               barmode='group',facet_col='run',facet_col_wrap=2,
               title='Business service spans matched to generated trace IDs')
    fig.update_layout(height=900)
    safe_write(fig,plots/'service-volume.html')
    fig=px.bar(pd.DataFrame(workflow_rows),x='workflow',y='completed',color='profile',
               barmode='group',facet_col='run',facet_col_wrap=2,
               title='Completed business workflows')
    safe_write(fig,plots/'workflow-frequency.html')
    if pod_resource_rows:
        fig=px.bar(pd.DataFrame(pod_resource_rows),x='pod',y='median_cpu_cores',
                   color='run',barmode='group',
                   title='Pod CPU activity by run (request identity unavailable)')
        fig.update_layout(height=560)
        safe_write(fig,plots/'pod-resource-distribution.html')
    fig=go.Figure()
    fig.add_annotation(text='Per-Pod business request counts UNVERIFIED: 0% of joined spans carry Kubernetes Pod identity',
                       x=.5,y=.5,xref='paper',yref='paper',showarrow=False,font=dict(size=17))
    fig.update_layout(title='Pod request distribution evidence gap',height=360,
                      xaxis=dict(visible=False),yaxis=dict(visible=False))
    safe_write(fig,plots/'pod-request-distribution.html')
    frame=pd.DataFrame(rows)
    fig=go.Figure()
    for name in ['service_coverage','edge_coverage','success_fraction']:
        fig.add_trace(go.Bar(name=name,x=frame['run'],y=frame[name]))
    fig.update_layout(title='No-fault workload profile comparison',barmode='group',
                      yaxis_title='Fraction',yaxis_range=[0,1.05])
    safe_write(fig,plots/'profiles-comparison.html')
    fig=go.Figure(go.Bar(x=frame['run'],
        y=[0 if pd.isna(value) else value for value in frame['networkpath_coverage']],
        marker_color='#2d8a7d'))
    fig.update_layout(title='NetworkPath route + packet proof during each run',
                      yaxis=dict(title='Verified path fraction',range=[0,1.05]),height=420)
    safe_write(fig,plots/'networkpath-coverage.html')
    print(json.dumps({'runs':len(rows),'service_coverage':[row['service_coverage'] for row in rows],
                      'edge_coverage':[row['edge_coverage'] for row in rows]}))


if __name__=='__main__':
    main()
