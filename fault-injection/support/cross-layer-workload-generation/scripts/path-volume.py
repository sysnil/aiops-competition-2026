#!/usr/bin/env python3
"""Conservative business-call lower bounds on paths with unique Pod endpoints."""

import argparse
import json
from pathlib import Path

import plotly.graph_objects as go


RUNS={'v2-MEDIUM-seed41004':'route-proof-v2-seed41004.json',
      'v2-MEDIUM-seed41005':'route-proof-v2-seed41005.json'}


def read(path): return json.loads(path.read_text())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    graph=read(args.root/'catalog/service-call-graph.json')
    counts={row['name']:len(row['pods']) for row in graph['services']}
    output=[]
    for run,proof_name in RUNS.items():
        traces=read(args.root/'observations'/f'{run}-trace-coverage.json')
        proof=read(args.root/'observations'/proof_name)
        for path in sorted(proof['verified_paths']):
            rows=[row for row in proof['results'] if row['status']=='VERIFIED' and
                  row['network_path']==path]
            unique=[row for row in rows if counts.get(row['edge'].split('->')[0])==1 and
                    counts.get(row['edge'].split('->')[1])==1]
            lower=sum(traces['edges'].get(row['edge'],0) for row in unique)
            output.append({'run':run,'path':path,'verified_packet_flows':len(rows),
                'attributed_trace_call_lower_bound':lower if unique else None,
                'attribution_edges':[row['edge'] for row in unique],
                'unknown_reason':None if unique else
                    'Both candidate edges involve a replicated service without Pod identity in traces'})
    target=args.root/'observations/path-volume-lower-bounds.json'
    target.write_text(json.dumps({'source':'joined exact-ID traces + fresh packet proof + unique Pod placement',
        'warning':'Call counts are lower bounds, not full NetworkPath traffic volume',
        'rows':output},indent=2)+'\n')
    fig=go.Figure()
    for run in RUNS:
        rows=[row for row in output if row['run']==run]
        fig.add_trace(go.Bar(name=run,x=[row['path'] for row in rows],
            y=[row['attributed_trace_call_lower_bound'] for row in rows],
            text=[str(row['attributed_trace_call_lower_bound']) if row['attributed_trace_call_lower_bound']
                  is not None else 'unattributed' for row in rows],textposition='auto'))
    fig.update_layout(template='plotly_white',barmode='group',height=530,
        title='NetworkPath business-call lower bounds (unique Pod endpoints only)',
        yaxis_title='Joined trace calls, lower bound')
    fig.write_html(args.root/'figures/networkpath-call-lower-bounds.html',include_plotlyjs=True)
    print(json.dumps(output))


if __name__=='__main__': main()
