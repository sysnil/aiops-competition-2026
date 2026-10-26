#!/usr/bin/env python3
"""Build reproducible aggregate plots from archived rehearsal summaries."""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


ROOT=Path('/root/cross-layer-observability-control-rehearsal')
IDS=('OC-D11-S1','OC-D11-S2','OC-D12-S1','OC-D12-S2')


def read(path):return json.loads(path.read_text())


def main():
    output=ROOT/'figures'
    output.mkdir(exist_ok=True)
    coverage=[]
    before=read(ROOT/'observations/pre-change-identity-audit/audit.json')
    clean=read(ROOT/'observations/OC-CLEAN-S1-identity-audit/audit.json')
    coverage.extend([{'window':'before repair','coverage':before['uid_coverage_fraction']},
                     {'window':'clean after repair','coverage':clean['uid_coverage_fraction']}])
    for name in ('OC-D06-S1','OC-D06-S2',*IDS):
        audit=read(ROOT/'attempts'/name/'identity-audit/audit.json')
        coverage.append({'window':name,'coverage':audit['uid_coverage_fraction']})
    fig=px.bar(pd.DataFrame(coverage),x='window',y='coverage',
               title='Ordinary business server-span Pod UID coverage')
    fig.update_layout(template='plotly_white',height=500,yaxis_range=[0,1.05])
    fig.write_html(output/'pod-uid-coverage.html',include_plotlyjs=True)

    pod_rows=[]
    for name in ('OC-D06-S1','OC-D06-S2'):
        summary=read(ROOT/'attempts'/name/'pod-analysis/summary.json')
        for role,uid in (('target',summary['target']['uid']),('normal sibling',summary['control']['uid'])):
            for row in summary['pod_trace_counts'][uid]:
                pod_rows.append({'run':name,'role':role,'minute':row['minute'],
                                 'ordinary_request_trace_ids':row['request_trace_ids']})
    fig=px.line(pd.DataFrame(pod_rows),x='minute',y='ordinary_request_trace_ids',
                color='role',facet_col='run',markers=True,
                title='UID-bound ordinary frontend requests across D06 repeats')
    fig.add_vrect(x0=1.5,x1=2.5,fillcolor='#e8ac6d',opacity=.22,line_width=0)
    fig.update_layout(template='plotly_white',height=520)
    fig.write_html(output/'d06-pod-request-comparison.html',include_plotlyjs=True)

    controls=[];probes=[]
    for name in IDS:
        control=read(ROOT/'attempts'/name/'control-analysis-v2/summary.json')
        probe=read(ROOT/'attempts'/name/'network-probe-analysis/network-probe-summary.json')
        for row in control['bins']:
            if row['stream'] in ('image_asset','cart_view'):
                controls.append({'run':name,'stream':row['stream'],'minute':row['minute_index'],
                    'successful_requests':row['successful'],
                    'usable_edge_spans':row['verified_call_edge_spans']})
        for phase,row in probe['phases'].items():
            probes.append({'run':name,'phase':phase,
                'throughput_mbps':row['throughput_bps_median']/1e6 if row['throughput_bps_median'] is not None else None,
                'loss_percent':row['loss_fraction_median']*100 if row['loss_fraction_median'] is not None else None})
    frame=pd.DataFrame(controls)
    fig=px.line(frame,x='minute',y='successful_requests',color='run',
                facet_row='stream',markers=True,
                title='Fixed ordinary business-control request counts during network faults')
    fig.add_hline(y=30,line_dash='dash',line_color='#bc573e')
    fig.add_vrect(x0=1.5,x1=2.5,fillcolor='#e8ac6d',opacity=.22,line_width=0)
    fig.update_layout(template='plotly_white',height=720)
    fig.write_html(output/'network-controls-by-minute.html',include_plotlyjs=True)
    fig=px.bar(pd.DataFrame(probes),x='run',y='throughput_mbps',color='phase',
               barmode='group',title='NetworkPath probe throughput across independent repeats')
    fig.update_layout(template='plotly_white',height=520)
    fig.write_html(output/'network-probe-throughput.html',include_plotlyjs=True)
    print(json.dumps({'status':'FIGURES_COMPLETE','files':[
        path.name for path in output.glob('*.html')]}))


if __name__=='__main__':main()
