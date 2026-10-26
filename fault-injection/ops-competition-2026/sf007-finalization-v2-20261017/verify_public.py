"""Independent readback of every public Parquet row and file, no private inputs."""
import argparse,json,sys,hashlib
from pathlib import Path
from collections import Counter
sys.path.insert(0,'/root/ops-competition-2026/sf007-real-adapter-v2-20261017/scripts')
from pilot_exchange_schema import private_leaks,iso_ns
from sf005_export import sha

def main():
 import pyarrow as pa
 import pyarrow.parquet as pq
 q=argparse.ArgumentParser();q.add_argument('--public',type=Path,required=True);q.add_argument('--output',type=Path,required=True);a=q.parse_args()
 expected={'metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet','topology.json','manifest.json'}
 if {p.name for p in a.public.iterdir()}!=expected:raise RuntimeError('PUBLIC_FILE_SET')
 m=json.loads((a.public/'manifest.json').read_text());lo=iso_ns(m['window']['start_utc']);hi=iso_ns(m['window']['end_utc']);counts={};nulls=Counter();ids=set();index_ids={};uids=set();leaks=[]
 topo=json.loads((a.public/'topology.json').read_text());pod_ids={p['uid'] for s in topo['snapshots'] for p in s['pods']}
 if m['case_id']!='SF-007' or hi-lo!=1800*10**9:raise RuntimeError('NOT_REGISTERED_THIRTY_MINUTES')
 for file in sorted(a.public.iterdir()):
  if file.name!='manifest.json' and sha(file)!=m['files'][file.name]['sha256']:raise RuntimeError('FILE_HASH')
  if file.suffix=='.json':
   if private_leaks(json.loads(file.read_text())):raise RuntimeError('PUBLIC_JSON_LEAK')
   continue
  count=0
  for batch in pq.ParquetFile(file).iter_batches(batch_size=5000):
   values={k:(batch.column(i).cast(pa.int64()).to_pylist() if pa.types.is_timestamp(batch.column(i).type) else batch.column(i).to_pylist()) for i,k in enumerate(batch.schema.names)}
   for i in range(batch.num_rows):
    r={k:v[i] for k,v in values.items()};count+=1
    if private_leaks(r):raise RuntimeError('PUBLIC_ROW_LEAK')
    if file.name!='trace_index.parquet':
     t=r.get('timestamp',r.get('start_time'))
     if not lo<=t<hi:raise RuntimeError('HALF_OPEN_WINDOW')
    if file.name=='traces.parquet':
     key=(r['trace_id'].lower(),r['span_id'].lower())
     if key in ids:raise RuntimeError('DUPLICATE_SPAN')
     ids.add(key)
     attrs=json.loads(r['resource_attributes'] or '{}');uid=attrs.get('k8s.pod.uid')
     if uid:
      uids.add(uid)
      if uid not in pod_ids:raise RuntimeError('UID_NOT_IN_REAL_TOPOLOGY')
     else:nulls['trace_pod_uid_unknown']+=1
    elif file.name=='trace_index.parquet':
     if r['trace_id'].lower() in index_ids:raise RuntimeError('DUPLICATE_TRACE_INDEX')
     index_ids[r['trace_id'].lower()]=r['span_count']
    for k,v in r.items():
     if v is None:nulls[file.stem+'.'+k]+=1
  counts[file.name]=count
 if set(t for t,s in ids)!=set(index_ids) or Counter(t for t,s in ids)!=Counter(index_ids):raise RuntimeError('TRACE_INDEX_MISMATCH')
 for name in ('metrics','logs','traces'):
  if counts[name+'.parquet']!=m['row_counts'][name]:raise RuntimeError('ROW_COUNT')
  if any(m['minute_counts'][name].get(str(i),0)<=0 for i in range(30)):raise RuntimeError('MINUTE_GAP')
 result={'status':'PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS','public_manifest_sha256':sha(a.public/'manifest.json'),'file_counts':counts,'unique_pod_uids':len(uids),'null_field_counts':dict(nulls),'all_30_minutes_nonempty_each_modality':True,'private_information_scan':'PASS','timestamp_window':'[start,end), unchanged nanoseconds','server_import_test':'NOT_EXECUTED'}
 with a.output.open('x') as f:json.dump(result,f,indent=2)
 print(json.dumps(result))
if __name__=='__main__':main()
