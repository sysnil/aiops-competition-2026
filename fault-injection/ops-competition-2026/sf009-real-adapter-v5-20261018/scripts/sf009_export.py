#!/usr/bin/env python3
"""Export a completed SF-009 30-minute capture to public Parquet.

Input is immutable capture chunks and a separately prepared public time window.
All duplicate/conflicting/out-of-window rows are counted or rejected explicitly.
"""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import sys

from pilot_exchange_schema import (canonical,iso_ns,tags,trace_record,log_record,private_leaks,schemas)
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()

def read(p):return json.loads(p.read_text())
def pages(root,kind):
    for p in sorted((root/kind).glob('*.json')):
        for hit in read(p)['hits']['hits']:yield hit
def write_new(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2,ensure_ascii=False);f.write('\n')

def public_trace_source(source):
    """Keep anomaly evidence while removing an explicit Envoy injection flag."""
    retained=[];removed=0
    for tag in source.get('tags') or []:
        key=str(tag.get('key',''));value=str(tag.get('value',''))
        if 'x-envoy-fault-' in key.lower() or 'x-envoy-fault-' in value.lower():
            raise RuntimeError('Injected request header appeared in trace storage')
        if key=='response_flags' and value=='DI':
            removed+=1
        else:
            retained.append(tag)
    public=dict(source)
    if source.get('tags') is not None:public['tags']=retained
    if any('x-envoy-fault-' in str(key).lower() or 'x-envoy-fault-' in str(value).lower()
           for key,value in (source.get('tag') or {}).items()):
        raise RuntimeError('Injected request header appeared in span attributes')
    return public,removed

ACCESS_FLAGS = re.compile(r'^(\[[^\]]+\]\s+"[^"]+"\s+\d{3}\s+)(\S+)(\s+.*)$', re.DOTALL)


def public_log_source(source):
    body=source.get('body')
    if not isinstance(body,str) or ' DI ' not in body:
        return source,0
    if (source.get('attributes') or {}).get('event.name')!='proxy.access':
        raise RuntimeError('Unexpected explicit delay marker in non-proxy log')
    match=ACCESS_FLAGS.fullmatch(body)
    if not match or match.group(2)!='DI':
        raise RuntimeError('Cannot safely locate the explicit Envoy delay flag')
    public=dict(source)
    public['body']=match.group(1)+'?'+match.group(3)
    return public,1

def main():
    p=argparse.ArgumentParser();p.add_argument('--captures',type=Path,required=True)
    p.add_argument('--window',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--metric-metadata',type=Path)
    a=p.parse_args();started=time.monotonic()
    import pyarrow as pa
    import pyarrow.parquet as pq
    window=read(a.window);lo=iso_ns(window['start_utc']);hi=iso_ns(window['end_utc'])
    if hi-lo!=1800*10**9 or window['case_id']!='SF-009':raise RuntimeError('Only the registered SF-009 30-minute window is accepted')
    if a.output.exists() or a.work.exists():raise RuntimeError('Export cannot overwrite a prior attempt')
    a.output.mkdir(parents=True,mode=0o700);a.work.mkdir(parents=True,mode=0o700)
    db=sqlite3.connect(a.work/'identity.sqlite');db.execute('CREATE TABLE spans(t TEXT,s TEXT,h TEXT,service TEXT,PRIMARY KEY(t,s))')
    db.execute('CREATE TABLE logs(k TEXT PRIMARY KEY,h TEXT)');db.execute('CREATE TABLE metrics(k TEXT PRIMARY KEY,v TEXT)')
    chunks=sorted(a.captures.glob('chunk-*'))
    if len(chunks)!=6:raise RuntimeError('Six complete immutable chunks required')
    if any(read(chunk/'quality.json')['status']!='CHUNK_QUALITY_PASS' for chunk in chunks):
        raise RuntimeError('An immutable chunk quality audit did not pass')
    expected=lo;inputs={};duplicates=Counter();counts=Counter();minutes={k:Counter() for k in ('metrics','logs','traces')}
    bounds={k:[None,None] for k in minutes};unknown=Counter();topology=[];trace_ids=set()
    registry=read(a.metric_metadata).get('data',{}) if a.metric_metadata else {}
    # First pass indexes actual parent identities; no inferred parent service.
    for chunk in chunks:
        first=chunk/'captures/during';final=chunk/'trace-late-audit/during';m=read(first/'capture-manifest.json')
        left,right=iso_ns(m['window']['start_utc']),iso_ns(m['window']['end_utc'])
        if left!=expected or right-left not in (300*10**9,600*10**9):raise RuntimeError('Capture window gap/overlap')
        expected=right
        if m['trace_source']!='JAEGER_OPENSEARCH_STORAGE':raise RuntimeError('Unexpected trace source')
        inputs[str(chunk.name)]=sha(first/'capture-manifest.json')
        for rel,digest in m['sha256'].items():
            if sha(first/rel)!=digest:raise RuntimeError('First capture hash mismatch')
        for hit in pages(final,'trace-storage-pages'):
            s=hit['_source'];ts=int(s['startTime'])*1000
            if not left<=ts<right:raise RuntimeError('Trace outside its exact capture window')
            key=(s['traceID'].lower(),s['spanID'].lower());digest=hashlib.sha256(canonical(s).encode()).hexdigest()
            old=db.execute('SELECT h FROM spans WHERE t=? AND s=?',key).fetchone()
            if old:
                if old[0]!=digest:raise RuntimeError('Conflicting duplicate span')
                duplicates['traces']+=1
            else:db.execute('INSERT INTO spans VALUES(?,?,?,?)',(*key,digest,s['process']['serviceName']))
        snap=read(first/'kubernetes/pods.json')
        topology.append({'observed_at_utc':m['metadata_observed_utc'],'pods':[{
            'name':r['metadata']['name'],'uid':r['metadata']['uid'],'namespace':r['metadata']['namespace'],
            'service':r['metadata'].get('labels',{}).get('opentelemetry.io/name'),
            'node':r['spec'].get('nodeName'),'pod_ip':r['status'].get('podIP'),
            'containers':[{'name':c['name'],'container_id':c.get('containerID'),'restart_count':c.get('restartCount')}
                for c in r['status'].get('containerStatuses',[])]} for r in snap['items']]})
        db.commit()
    if expected!=hi:raise RuntimeError('Thirty-minute window end not covered')
    schema=schemas(pa);writers={k:pq.ParquetWriter(a.output/(k+'.parquet'),v,compression='zstd',compression_level=3) for k,v in schema.items()}
    buffers={k:[] for k in schema};written=set();association=Counter()
    def emit(kind,row,stamp):
        leaks=private_leaks(row)
        if leaks:raise RuntimeError('Private content found, public package blocked: '+repr(leaks))
        if not lo<=stamp<hi:raise RuntimeError('Export record outside rehearsal boundary')
        counts[kind]+=1;minutes[kind][str((stamp-lo)//(60*10**9))]+=1
        bounds[kind][0]=stamp if bounds[kind][0] is None else min(bounds[kind][0],stamp)
        bounds[kind][1]=stamp if bounds[kind][1] is None else max(bounds[kind][1],stamp)
        buffers[kind].append(row)
        if len(buffers[kind])>=5000:
            writers[kind].write_table(pa.Table.from_pylist(buffers[kind],schema=schema[kind]));buffers[kind].clear()
    try:
        for chunk in chunks:
            first=chunk/'captures/during';final=chunk/'trace-late-audit/during'
            cm=read(first/'capture-manifest.json');chunklo=iso_ns(cm['window']['start_utc']);chunkhi=iso_ns(cm['window']['end_utc'])
            for file in sorted((first/'metrics').glob('*.json')):
                for series in read(file)['data']['result']:
                    labels=series['metric'];name=labels.get('__name__')
                    if not name:raise RuntimeError('Original metric name missing')
                    info=registry.get(name) or [];meta=info[0] if len(info)==1 else {}
                    if not meta:unknown['metric_metadata_series']+=1
                    for t,v in series['values']:
                        ts=int(Decimal(str(t))*10**9)
                        # query_range includes the final boundary; explicitly count the one excluded endpoint.
                        if not chunklo<=ts<chunkhi:
                            # Prometheus query_range rounds evaluation times to milliseconds.
                            # Implement the ALREADY registered half-open partition without shifting timestamps.
                            if chunklo-1000000<=ts<chunklo or chunkhi<=ts<=chunkhi+1000000:
                                duplicates['excluded_metric_query_boundary_rounding']+=1;continue
                            raise RuntimeError('Metric outside registered chunk partition by more than query precision')
                        key=hashlib.sha256((canonical(labels)+'@'+str(ts)).encode()).hexdigest()
                        old=db.execute('SELECT v FROM metrics WHERE k=?',(key,)).fetchone()
                        if old:
                            if old[0]!=str(v):raise RuntimeError('Conflicting duplicate metric')
                            duplicates['metrics']+=1;continue
                        db.execute('INSERT INTO metrics VALUES(?,?)',(key,str(v)))
                        emit('metrics',{'timestamp':ts,'metric_name':name,'value':float(v),'labels':canonical(labels),
                            'service':labels.get('service_name') or labels.get('service'),
                            'pod':labels.get('pod') or labels.get('k8s_pod_name'),
                            'node':labels.get('node') or labels.get('k8s_node_name') or labels.get('kubernetes_io_hostname'),
                            'unit':meta.get('unit') or None,'metric_type':meta.get('type') or None,'source':'PROMETHEUS'},ts)
            for hit in pages(first,'logs'):
                key=hit['_index']+'/'+hit['_id'];s=hit['_source'];digest=hashlib.sha256(canonical(s).encode()).hexdigest()
                old=db.execute('SELECT h FROM logs WHERE k=?',(key,)).fetchone()
                if old:
                    if old[0]!=digest:raise RuntimeError('Conflicting duplicate log')
                    duplicates['logs']+=1;continue
                db.execute('INSERT INTO logs VALUES(?,?)',(key,digest))
                public_log,removed=public_log_source(s)
                unknown['redacted_explicit_envoy_delay_log_flags']+=removed
                r=log_record(public_log)
                if r['trace_id']:
                    association['logs_with_trace_id']+=1
                    if db.execute('SELECT 1 FROM spans WHERE t=? LIMIT 1',(r['trace_id'].lower(),)).fetchone():association['logs_linked_to_exported_trace']+=1
                emit('logs',r,r['timestamp'])
            for hit in pages(final,'trace-storage-pages'):
                s=hit['_source'];key=(s['traceID'].lower(),s['spanID'].lower())
                if key in written:continue
                written.add(key);trace_ids.add(key[0])
                parents=[r for r in s.get('references') or [] if r.get('refType')=='CHILD_OF'];parent_service=None
                if parents:
                    parent=parents[0];found=db.execute('SELECT service FROM spans WHERE t=? AND s=?',
                        (parent.get('traceID',s['traceID']).lower(),parent['spanID'].lower())).fetchone()
                    association['spans_with_parent_reference']+=1
                    if found:parent_service=found[0];association['parent_identity_joined']+=1
                    else:association['parent_absent_from_window_or_unrecorded']+=1
                resource=tags(s['process'].get('tags'))
                if resource.get('k8s.pod.uid'):association['spans_with_pod_uid']+=1
                public_span,removed=public_trace_source(s)
                unknown['redacted_explicit_envoy_delay_flags']+=removed
                r=trace_record(public_span,parent_service);emit('traces',r,r['start_time'])
            db.commit()
        for k,rows in buffers.items():
            if rows:writers[k].write_table(pa.Table.from_pylist(rows,schema=schema[k]))
    finally:
        for w in writers.values():w.close()
        db.close()
    if any(counts[k]==0 or any(minutes[k][str(i)]==0 for i in range(30)) for k in schema):raise RuntimeError('Unexplained empty modality/minute; inspect retained partial export')
    indexes={};edges=Counter();metric_series=set()
    for batch in pq.ParquetFile(a.output/'metrics.parquet').iter_batches(columns=['labels']):
        metric_series.update(batch.column(0).to_pylist())
    for batch in pq.ParquetFile(a.output/'traces.parquet').iter_batches(columns=['trace_id','span_id','parent_span_id','parent_service','service','start_time','duration_ms','status']):
        values={n:(batch.column(i).cast(pa.int64()).to_pylist() if n=='start_time' else batch.column(i).to_pylist()) for i,n in enumerate(batch.schema.names)}
        for i,tid in enumerate(values['trace_id']):
            begin=values['start_time'][i];finish=begin+int(round(values['duration_ms'][i]*1000000))
            v=indexes.setdefault(tid,{'trace_id':tid,'start_time':begin,'end_time':finish,'span_count':0,'services':set(),'has_error':False,'unresolved_parent_count':0})
            v['start_time']=min(v['start_time'],begin);v['end_time']=max(v['end_time'],finish);v['span_count']+=1
            v['services'].add(values['service'][i]);v['has_error']=v['has_error'] or values['status'][i]=='ERROR'
            if values['parent_span_id'][i] and not values['parent_service'][i]:v['unresolved_parent_count']+=1
            parent,service=values['parent_service'][i],values['service'][i]
            if parent and parent!=service:edges[(parent,service)]+=1
    index_rows=[{**v,'services':canonical(sorted(v['services']))} for v in indexes.values()]
    index_schema=pa.schema([('trace_id',pa.string()),('start_time',pa.timestamp('ns',tz='UTC')),('end_time',pa.timestamp('ns',tz='UTC')),
        ('span_count',pa.int64()),('services',pa.string()),('has_error',pa.bool_()),('unresolved_parent_count',pa.int64())])
    pq.write_table(pa.Table.from_pylist(index_rows,schema=index_schema),a.output/'trace_index.parquet',compression='zstd',compression_level=3)
    if sum(r['span_count'] for r in index_rows)!=counts['traces'] or len(indexes)!=len(trace_ids):raise RuntimeError('Real trace index does not reconcile')
    topo={'case_id':'SF-009','kind':'observed_public_kubernetes_identity_snapshots','snapshots':topology,'observed_service_edges':[{'source':s,'destination':t,'child_of_span_count':n} for (s,t),n in sorted(edges.items())]}
    if private_leaks(topo):raise RuntimeError('Topology private leak')
    write_new(a.output/'topology.json',topo)
    files={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(a.output.iterdir()) if p.is_file()}
    for k in schema:
        if pq.ParquetFile(a.output/(k+'.parquet')).metadata.num_rows!=counts[k]:raise RuntimeError('Parquet row count mismatch')
    manifest={'schema_version':'CONTEST_EXCHANGE_V2','case_id':'SF-009','window':window,
        'time_bounds':'start inclusive, end exclusive','files':files,'row_counts':dict(counts),'unique_trace_count':len(trace_ids),'metric_series_count':len(metric_series),'trace_index_rows':len(index_rows),
        'minute_counts':{k:dict(v) for k,v in minutes.items()},'timestamp_bounds_unix_ns':bounds,
        'schemas':{k:str(v) for k,v in schema.items()},'structured_fields_encoding':'Canonical JSON strings; null means unknown',
        'unknown_metric_metadata':'null, never inferred','raw_metric_counters':'not converted to rates',
        'trace_source':'JAEGER_OPENSEARCH_STORAGE','trace_status_null':'source did not emit explicit OTel status/error',
        'actual_server_import_compatibility':'NOT_TESTED_THIS_ROUND','private_leak_scan':'PASS'}
    write_new(a.output/'manifest.json',manifest)
    private={'status':'PUBLIC_EXPORT_STRUCTURAL_VALIDATION_PASS_PENDING_CASE_QUALITY','input_capture_manifest_sha256':inputs,
        'duplicate_and_boundary_accounting':dict(duplicates),'unknowns':dict(unknown),'cross_modal_association':dict(association),
        'exporter_sha256':sha(Path(__file__)),'source_30min_converter_sha256':sha(Path('/root/ops-competition-2026/30min-feasibility-20261015/scripts/preview_export.py')),'implementation_scope':'SF-009 exact 30-minute case ID and mandatory chunk-quality gate; metric boundary rule unchanged','elapsed_seconds':time.monotonic()-started,'parquet_version':pa.__version__,
        'public_manifest_sha256':sha(a.output/'manifest.json'),'source_records_silently_discarded':False,'trace_export_version':'immutable_plus240_audit_snapshot','observed_service_edge_count':len(edges),'metric_series_count':len(metric_series)}
    write_new(a.work/'export-report.json',private)
    print(json.dumps(private))

if __name__=='__main__':main()
