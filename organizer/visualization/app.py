"""Organizer-only, read-only explorer for imported semifinal Public data."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data import (CASE_IDS, counts, edge_summary, facets, load_case, log_summary,
                  logs, metric_inventory, metric_series, trace_list, trace_spans,
                  verify_files)


_taxonomy_bytes = Path(__file__).with_name('anomaly-taxonomy-v1.json').read_bytes()
if hashlib.sha256(_taxonomy_bytes).hexdigest() != \
        'e972da4a2f82407fb30f985463e61d6943ce3fda043ef76846b03be71ca7282a':
    raise RuntimeError('Published anomaly taxonomy checksum mismatch')
ANOMALY_TYPES = {
    row['id']: row['name_zh']
    for row in json.loads(_taxonomy_bytes)['types']
}


st.set_page_config(page_title='Semifinal | Case Explorer', page_icon='▦', layout='wide')
st.markdown('''<style>
.block-container {max-width: 1560px; padding-top: 1.2rem;}
h1 {font-size: 1.7rem !important; letter-spacing: 0;}
h2 {font-size: 1.25rem !important; letter-spacing: 0;}
[data-testid="stSidebar"] {background: #f4f7f6;}
[data-testid="stMetricValue"] {font-size: 1.25rem;}
</style>''', unsafe_allow_html=True)


@st.cache_data(ttl=30, show_spinner=False)
def case_info(case_id):
    return load_case(case_id)


@st.cache_data(ttl=120, show_spinner=False)
def facet_info(case_id, manifest_hash):
    return facets(load_case(case_id))


@st.cache_data(ttl=120, show_spinner=False)
def query_data(case_id, manifest_hash, query, start, end, *args, **kwargs):
    case = load_case(case_id)
    operation = {
        'metric_inventory': metric_inventory,
        'metric_series': metric_series,
        'log_summary': log_summary,
        'logs': logs,
        'trace_list': trace_list,
        'edge_summary': edge_summary,
        'counts': counts,
    }[query]
    return operation(case, start, end, *args, **kwargs)


@st.cache_data(ttl=120, show_spinner=False)
def query_trace(case_id, manifest_hash, trace_id):
    return trace_spans(load_case(case_id), trace_id)


def add_interval(fig, candidate):
    if candidate:
        fig.add_vrect(x0=candidate['start'], x1=candidate['end'],
            fillcolor='#c65b45', opacity=.16, line_width=0,
            annotation_text='Candidate', annotation_position='top left')
        fig.add_vline(x=candidate['start'], line_dash='dash', line_color='#a64231')
        fig.add_vline(x=candidate['end'], line_dash='dash', line_color='#a64231')
    fig.update_layout(template='plotly_white', margin=dict(l=12, r=12, t=45, b=20),
                      hovermode='x unified', legend_title_text='', height=350)
    return fig


def show_metrics(case, key, start, end, service, pod, edge, candidate):
    st.subheader('Metrics')
    options = facet_info(case['id'], key)['metrics']
    if not options:
        st.info('No metric series in this Public bundle.')
        return
    default = options.index('container_cpu_usage_seconds_total') \
        if 'container_cpu_usage_seconds_total' in options else 0
    metric = st.selectbox('Metric', options, index=default, key='metric_choice')
    frame = query_data(case['id'], key, 'metric_series', start, end,
                       metric, service, pod, edge)
    if frame.empty:
        st.info('No samples for this entity and time range.')
    else:
        frame['entity'] = frame.apply(lambda row: row['pod'] if row['pod'] !=
            'UNATTRIBUTED' else row['service'], axis=1)
        chart_field = 'value'
        unit = frame['unit'].dropna().iloc[0] if frame['unit'].notna().any() else 'Value'
        if metric.endswith('_total'):
            frame = frame.sort_values(['entity', 'minute'])
            frame['rate'] = frame.groupby('entity')['value'].diff() / \
                            frame.groupby('entity')['minute'].diff().dt.total_seconds()
            frame.loc[frame['rate'] < 0, 'rate'] = float('nan')
            chart_field, unit = 'rate', f'{unit}/s'
        top = frame.groupby('entity')['samples'].sum().nlargest(8).index
        plotted = frame[frame['entity'].isin(top)]
        fig = px.line(plotted, x='minute', y=chart_field, color='entity', markers=True,
                      title=metric, labels={'minute': 'UTC', chart_field: unit})
        st.plotly_chart(add_interval(fig, candidate), width="stretch")
        st.caption(f'{len(frame):,} minute/entity points · {int(frame.samples.sum()):,} raw samples; '
                   'counter rates use successive minute means and omit resets.' if chart_field == 'rate'
                   else f'{len(frame):,} minute/entity points · {int(frame.samples.sum()):,} raw samples; '
                   'lines show the mean of archived values in each minute.')
        with st.expander('Metric rows'):
            st.dataframe(frame, width="stretch", hide_index=True, height=280)
    st.markdown('#### Available metric families')
    st.dataframe(query_data(case['id'], key, 'metric_inventory', start, end),
                 width="stretch", hide_index=True, height=260)


def show_logs(case, key, start, end, service, pod, edge, candidate):
    st.subheader('Logs')
    keyword = st.text_input('Keyword', placeholder='Search archived message text')
    summary = query_data(case['id'], key, 'log_summary', start, end, service, pod, edge)
    if not summary.empty:
        fig = px.bar(summary, x='minute', y='records', color='severity',
                     title='Logs per minute', color_discrete_map={
                     'ERROR': '#b34436', 'WARN': '#cf8a32', 'WARNING': '#cf8a32',
                     'INFO': '#338276', 'DEBUG': '#7193a0', 'UNKNOWN': '#888f96'})
        st.plotly_chart(add_interval(fig, candidate), width="stretch")
        totals = summary.groupby('severity', as_index=False)['records'].sum()
        st.dataframe(totals, width="stretch", hide_index=True)
    page = st.number_input('Page', min_value=1, max_value=1000, value=1, step=1)
    frame = query_data(case['id'], key, 'logs', start, end, service, pod,
                       keyword, edge, limit=300, offset=(page - 1) * 300)
    st.dataframe(frame, width="stretch", hide_index=True, height=420)
    st.caption('Raw Public logs. Unrecognized or absent level is UNKNOWN; no level is inferred.')


def show_waterfall(case, key, trace_id):
    spans = query_trace(case['id'], key, trace_id)
    if spans.empty:
        st.warning('Trace ID is not present in this imported Public bundle.')
        return
    spans = spans.sort_values(['start_time', 'span_id']).copy()
    base = pd.to_datetime(spans['start_time'], utc=True).min()
    spans['offset_ms'] = (pd.to_datetime(spans['start_time'], utc=True) - base).dt.total_seconds() * 1000
    spans['duration_ms'] = spans['duration_ms'].fillna(0).clip(lower=0)
    extent = float((spans['offset_ms'] + spans['duration_ms']).max())
    spans['label'] = [f'{i:03d} {service}' for i, service in enumerate(spans.service, 1)]
    fig = go.Figure(go.Bar(x=spans['duration_ms'], y=spans['label'],
        base=spans['offset_ms'], orientation='h',
        marker_color=[px.colors.qualitative.Safe[i % len(px.colors.qualitative.Safe)]
                      for i in pd.factorize(spans.service)[0]],
        customdata=spans[['service', 'operation_name', 'span_id', 'parent_span_id']].fillna('').to_numpy(),
        hovertemplate='%{customdata[0]} / %{customdata[1]}<br>span %{customdata[2]}'
                      '<br>parent %{customdata[3]}<br>duration %{x:.3f} ms<extra></extra>'))
    fig.update_layout(template='plotly_white', height=max(330, min(1200, 24 * len(spans))),
                      title=f'Trace {trace_id}', xaxis_title='Milliseconds from first span',
                      yaxis={'autorange': 'reversed'}, showlegend=False,
                      margin=dict(l=15, r=15, t=42, b=18))
    fig.update_xaxes(range=[0, max(1.0, extent * 1.02)])
    st.caption(f'{len(spans)} real spans · {extent:.2f} ms observed extent')
    st.plotly_chart(fig, width="stretch")
    st.dataframe(spans[['start_time', 'service', 'operation_name', 'span_id',
                        'parent_span_id', 'duration_ms', 'status']],
                 width="stretch", hide_index=True, height=320)


def show_traces(case, key, start, end, service, pod, edge):
    st.subheader('Traces')
    frame = query_data(case['id'], key, 'trace_list', start, end, service, pod, edge)
    if frame.empty:
        st.info('No Trace IDs for this filter and time range.')
        return
    selected = st.dataframe(frame, on_select='rerun', selection_mode='single-row',
                            width="stretch", hide_index=True, height=330)
    selection = selected.selection.rows
    if selection:
        trace_id = frame.iloc[selection[0]]['trace_id']
        st.caption(f'Selected from Trace list: {trace_id}')
    else:
        trace_id = st.selectbox('Trace ID', frame.trace_id.tolist())
    show_waterfall(case, key, trace_id)


def show_topology(case, key, start, end, service, edge):
    st.subheader('Topology')
    topology = case['manifest'].get('public_topology', {})
    snapshots = topology.get('snapshots', [])
    if not snapshots:
        st.info('No Public topology snapshots in this case.')
        return
    boundary = start + (end - start) / 2
    chosen = min(snapshots, key=lambda item: abs((pd.Timestamp(item['observed_at_utc']) -
                                                  pd.Timestamp(boundary)).total_seconds()))
    st.caption(f"Public placement snapshot: {chosen['observed_at_utc']} UTC")
    pods = pd.DataFrame(chosen.get('pods', []))
    if service and 'service' in pods:
        pods = pods[pods.service == service]
    if not pods.empty:
        st.dataframe(pods[['service', 'name', 'uid', 'node', 'pod_ip']].fillna(''),
                     width="stretch", hide_index=True, height=300)
    frame = query_data(case['id'], key, 'edge_summary', start, end, service, edge)
    if frame.empty:
        st.info('No observed cross-service calls in this time range.')
        return
    shown = frame.head(24).copy()
    names = sorted(set(shown.source) | set(shown.destination))
    ids = {name: index for index, name in enumerate(names)}
    fig = go.Figure(go.Sankey(node={'label': names, 'color': '#24796f',
                                    'pad': 12, 'thickness': 12},
        link={'source': shown.source.map(ids), 'target': shown.destination.map(ids),
              'value': shown.calls, 'customdata': shown[['p95_ms', 'errors']].to_numpy(),
              'hovertemplate': '%{source.label} → %{target.label}<br>calls %{value}'
                               '<br>p95 %{customdata[0]:.2f} ms'
                               '<br>errors %{customdata[1]}<extra></extra>'}))
    fig.update_layout(height=560, margin=dict(l=8, r=8, t=8, b=8), font_size=11)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(frame, width="stretch", hide_index=True, height=300)
    st.caption('Dependency edges come from archived parent_service and service Span fields. '
               'Placement comes from the nearest Public Kubernetes snapshot.')


def show_review(case, key, candidate, start, end, service, pod, edge):
    st.subheader('Anomaly review')
    if not candidate:
        st.info('Enter a candidate type, entity and interval in the sidebar to inspect aligned Public evidence.')
        return
    st.write({'candidate_type': candidate['type'], 'entity': candidate['entity'],
              'description': candidate.get('description') or None,
              'start_utc': candidate['start'].isoformat(),
              'end_utc': candidate['end'].isoformat(),
              'source': 'reviewer input in this browser session; not GT'})
    cstart, cend = candidate['start'], candidate['end']
    candidate_service = candidate['entity'] if candidate['entity'] in \
        facet_info(case['id'], key)['services'] else None
    focus_service = service or candidate_service
    left = query_data(case['id'], key, 'edge_summary', case['start'], cstart,
                      service, edge) if cstart > case['start'] else pd.DataFrame()
    right = query_data(case['id'], key, 'edge_summary', cstart, cend,
                       service, edge)
    if not right.empty:
        right['edge'] = right.source + ' → ' + right.destination
        right['candidate_related'] = right.apply(lambda row: candidate['entity'] in
            (row['source'], row['destination'], row['edge']), axis=1)
        right = right.sort_values(['candidate_related', 'calls'], ascending=[False, False])
        st.markdown('#### Candidate-window call edges')
        st.dataframe(right[['candidate_related', 'edge', 'calls', 'errors', 'p95_ms']],
                     width="stretch", hide_index=True, height=300)
        if not left.empty:
            left['edge'] = left.source + ' → ' + left.destination
            comparison = right[['edge', 'p95_ms', 'calls']].merge(
                left[['edge', 'p95_ms', 'calls']], on='edge', how='left',
                suffixes=('_candidate', '_earlier'))
            st.markdown('#### Earlier window vs candidate')
            st.dataframe(comparison, width="stretch", hide_index=True)
    else:
        st.info('No matching CallEdge Span in the candidate interval.')
    logs_frame = query_data(case['id'], key, 'logs', cstart, cend, focus_service, pod,
                            '', edge, limit=150, offset=0)
    trace_frame = query_data(case['id'], key, 'trace_list', cstart, cend,
                             focus_service, pod, edge, 80)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown('#### Logs in interval')
        st.dataframe(logs_frame, width="stretch", hide_index=True, height=350)
    with col2:
        st.markdown('#### Traces in interval')
        st.dataframe(trace_frame, width="stretch", hide_index=True, height=350)
    st.caption('This view compares Public evidence only. It does not approve or write anomaly GT.')


def main():
    st.title('Semifinal Case Explorer')
    st.caption('Organizer view · imported Public observability archive · UTC')
    readiness = {name: case_info(name) for name in CASE_IDS}
    case_id = st.sidebar.selectbox('Case', CASE_IDS,
        format_func=lambda name: name + (' · ready' if readiness[name] else ' · 数据未就绪'))
    case = readiness[case_id]
    if not case:
        st.warning(f'{case_id}：数据未就绪。导入 Public 数据后刷新页面即可切换。')
        st.dataframe(pd.DataFrame([{'case_id': name,
            'status': 'READY' if readiness[name] else '数据未就绪'} for name in CASE_IDS]),
            width="stretch", hide_index=True)
        return
    key = case['manifest'].get('immutable_manifest_hash') or str(case['manifest_path'].stat().st_mtime_ns)
    options = facet_info(case_id, key)
    span_minutes = int((case['end'] - case['start']).total_seconds() / 60)
    minutes = st.sidebar.slider('UTC range · minute offset', 0, span_minutes,
                                (0, span_minutes), step=1)
    start = case['start'] + timedelta(minutes=minutes[0])
    end = case['start'] + timedelta(minutes=minutes[1])
    if start >= end:
        st.error('Choose a non-empty time range.')
        return
    service_name = st.sidebar.selectbox('Service', ['All', *options['services']])
    pod_name = st.sidebar.selectbox('Pod', ['All', *options['pods']])
    edge_names = ['All', *[f'{a} → {b}' for a, b in options['edges']]]
    edge_label = st.sidebar.selectbox('CallEdge', edge_names)
    edge = options['edges'][edge_names.index(edge_label) - 1] if edge_label != 'All' else None
    service = None if service_name == 'All' else service_name
    pod = None if pod_name == 'All' else pod_name
    st.sidebar.caption(f'{start.isoformat()} to {end.isoformat()} · half-open UTC window')
    with st.sidebar.expander('Review candidate · session only'):
        kind = st.selectbox('Candidate type', ['None', *ANOMALY_TYPES],
                            format_func=lambda value: value if value == 'None'
                            else f'{value} · {ANOMALY_TYPES[value]}')
        entity = st.text_input('Candidate entity', max_chars=128)
        description = st.text_area('Observed deviation', max_chars=1000) if \
            kind == 'OTHER_OBSERVABLE_ANOMALY' else ''
        begin = st.number_input('Start minute', min_value=0, max_value=span_minutes,
                                 value=min(20, span_minutes))
        finish = st.number_input('End minute', min_value=0, max_value=span_minutes,
                                  value=min(25, span_minutes))
    candidate = None
    if kind != 'None' and entity and begin < finish and \
            (kind != 'OTHER_OBSERVABLE_ANOMALY' or len(description.strip()) >= 8):
        candidate = {'type': kind, 'entity': entity,
                     'description': description.strip() if description else None,
                     'start': case['start'] + timedelta(minutes=begin),
                     'end': case['start'] + timedelta(minutes=finish)}
    elif kind == 'OTHER_OBSERVABLE_ANOMALY' and len(description.strip()) < 8:
        st.sidebar.caption('OTHER_OBSERVABLE_ANOMALY requires a specific observed deviation.')
    total = query_data(case_id, key, 'counts', case['start'], case['end'])
    a, b, c, d = st.columns(4)
    a.metric('Case', case_id)
    b.metric('Metrics', f"{total['metrics']:,}")
    c.metric('Logs', f"{total['logs']:,}")
    d.metric('Spans', f"{total['traces']:,}")
    st.caption(f"Archive: {case['start'].isoformat()} to {case['end'].isoformat()} "
               f"({span_minutes} min) · version {case['manifest']['version']} · "
               f"{total['trace_index']:,} Trace IDs")
    tabs = st.tabs(['Overview', 'Metrics', 'Logs', 'Traces', 'Topology', 'Review'])
    with tabs[0]:
        st.subheader('Public bundle')
        st.dataframe(pd.DataFrame([{'case_id': name,
            'status': 'READY' if readiness[name] else '数据未就绪'} for name in CASE_IDS]),
            width="stretch", hide_index=True, height=280)
        st.write({'schema': case['manifest']['dataset_schema_version'],
                  'modalities': case['manifest']['available_modalities'],
                  'topology_snapshots': len(case['manifest'].get('public_topology', {}).get('snapshots', [])),
                  'manifest_hash': case['manifest'].get('immutable_manifest_hash')})
        if st.button('Verify Public file SHA-256'):
            st.json(verify_files(case))
    with tabs[1]: show_metrics(case, key, start, end, service, pod, edge, candidate)
    with tabs[2]: show_logs(case, key, start, end, service, pod, edge, candidate)
    with tabs[3]: show_traces(case, key, start, end, service, pod, edge)
    with tabs[4]: show_topology(case, key, start, end, service, edge)
    with tabs[5]: show_review(case, key, candidate, start, end, service, pod, edge)


if __name__ == '__main__':
    main()
