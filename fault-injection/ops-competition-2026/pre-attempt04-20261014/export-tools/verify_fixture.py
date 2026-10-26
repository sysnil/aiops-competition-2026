"""Compare exported fixture with raw no-fault captures; never creates a formal question."""
import json,hashlib,math,sys
from pathlib import Path
from collections import Counter
from decimal import Decimal
import pyarrow as pa
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).parent/'schema'))
from pilot_exchange_schema import canonical,iso_ns,tags,log_record
ROOT=Path(sys.argv[1]);OUT=ROOT/'fixture-output'
read=lambda p:json.loads(Path(p).read_text())
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
    return h.hexdigest()
def rows(name):
    for batch in pq.ParquetFile(OUT/(name+'.parquet')).iter_batches(batch_size=5000):
        d={n:(batch.column(i).cast(pa.int64()).to_pylist() if pa.types.is_timestamp(batch.column(i).type) else batch.column(i).to_pylist()) for i,n in enumerate(batch.schema.names)}
        for j in range(batch.num_rows):yield {k:v[j] for k,v in d.items()}
manifest=read(OUT/'manifest.json');assert manifest['fixture'] is True and manifest['formal_case'] is False
assert all(sha(OUT/n)==v['sha256'] for n,v in manifest['files'].items())
expected_metrics={};expected_spans={};expected_logs=Counter();excluded=0;inside=0
for chunk in sorted((ROOT/'capture').glob('chunk-*')):
    first=chunk/'captures/during';m=read(first/'capture-manifest.json');lo,hi=iso_ns(m['window']['start_utc']),iso_ns(m['window']['end_utc'])
    for f in (first/'metrics').glob('*.json'):
        for series in read(f)['data']['result']:
            for t,v in series['values']:
                ns=int(Decimal(str(t))*10**9)
                if lo<=ns<hi:expected_metrics[(canonical(series['metric']),ns)]=float(v);inside+=1
                else:excluded+=1
    for f in (first/'logs').glob('*.json'):
        for hit in read(f)['hits']['hits']:expected_logs[hashlib.sha256(canonical(log_record(hit['_source'])).encode()).hexdigest()]+=1
    for f in (chunk/'trace-late-audit/during/trace-storage-pages').glob('*.json'):
        for hit in read(f)['hits']['hits']:
            s=hit['_source'];uid=tags(s['process'].get('tags')).get('k8s.pod.uid')
            expected_spans[(s['traceID'],s['spanID'])]=(s['startTime']*1000,s['duration']/1000,uid)
count=0
for r in rows('metrics'):
    value=expected_metrics.pop((r['labels'],r['timestamp']));assert value==r['value'] or math.isnan(value) and math.isnan(r['value']);count+=1
assert not expected_metrics
uid_count=0;span_count=0
for r in rows('traces'):
    ts,duration,uid=expected_spans.pop((r['trace_id'],r['span_id']))
    assert (r['start_time'],r['duration_ms'])==(ts,duration)
    assert (json.loads(r['resource_attributes'] or '{}').get('k8s.pod.uid'))==uid
    uid_count+=uid is not None;span_count+=1
assert not expected_spans
actual_logs=Counter();submicro=0
for r in rows('logs'):
    actual_logs[hashlib.sha256(canonical(r).encode()).hexdigest()]+=1;submicro+=r['timestamp']%1000!=0
assert actual_logs==expected_logs
assert all(pq.ParquetFile(OUT/(name+'.parquet')).schema_arrow.field(field).type==pa.timestamp('ns',tz='UTC') for name,field in [('metrics','timestamp'),('logs','timestamp'),('traces','start_time')])
result={'status':'NO_FAULT_PARQUET_FIXTURE_PASS','metrics_rows':count,'raw_in_window_samples':inside,'excluded_outside_window_query_endpoints':excluded,'all_in_window_samples_preserved':True,'trace_rows':span_count,'trace_span_ids_preserved':True,'spans_with_verified_pod_uid':uid_count,'log_rows':sum(actual_logs.values()),'logs_with_submicrosecond_precision':submicro,'nanosecond_schema_roundtrip':True,'raw_captures_modified':False,'formal_public_question_created':False,'fixture_manifest_sha256':sha(OUT/'manifest.json')}
with (ROOT/'fixture-verification.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
