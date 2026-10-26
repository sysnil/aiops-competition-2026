"""Read-only six-file gate; never imports a database, serves files, or rewrites data."""
import argparse,hashlib,json,os,re,stat,struct,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
import pyarrow as pa
import pyarrow.parquet as pq
FILES=['metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet','topology.json','manifest.json']
EXPECTED_SHA='8ad8c5eb25e48a33ebf776a40db0f3fbe62284ee7c26df3e4d04139532db0eaa'
TOTAL=77945722
COUNTS={'metrics.parquet':80358,'logs.parquet':567346,'traces.parquet':817529,'trace_index.parquet':119091}
LO=1791969145156656000
HI=1791972745156656000
FORBIDDEN=['SF-PILOT-01','COMP-SF01-','/root/ops-competition-2026','/data/stage5-v3/ops-competition-2026',
 'private_injection','injection-plan','injection-record','anomaly-candidates','anomaly-gt','review-notes.md','SIGSTOP','resource_memory_holder.py',
 'pilot_controller.py','pilot_observer.py','schedule-lock','run-manifest','fault_target','fault_type','root_layer','root_entity',
 'stage5-workload-rehearsal','oc-business-workflow','ops-inject-sf-pilot']
def digest(b):return hashlib.sha256(b).hexdigest()
def ns(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'));return int(d.timestamp())*10**9+d.microsecond*1000

def validate(read_bytes,inventory):
 started=time.monotonic()
 if sorted(x['name'] for x in inventory)!=sorted(FILES):raise RuntimeError('SIX_FILE_WHITELIST')
 if any(not x['regular'] or x['symlink'] for x in inventory):raise RuntimeError('NONREGULAR_OR_SYMLINK')
 if sum(x['bytes'] for x in inventory)!=TOTAL:raise RuntimeError('PACKAGE_TOTAL_SIZE')
 manifest_bytes=read_bytes('manifest.json')
 if digest(manifest_bytes)!=EXPECTED_SHA:raise RuntimeError('MANIFEST_SHA')
 m=json.loads(manifest_bytes)
 if m['case_id']!='SF-001' or set(m['files'])!=set(FILES)-{'manifest.json'}:raise RuntimeError('MANIFEST_CONTENT')
 if ns(m['window']['start_utc'])!=LO or ns(m['window']['end_utc'])!=HI or HI-LO!=3600*10**9:raise RuntimeError('WINDOW')
 results={};schema={};bounds={};leaks=[];minute={};trace_counts={};index_counts={};parquet_bytes=0
 def scan(row):
  s=json.dumps(row,ensure_ascii=True,default=str)
  hits=[k for k in FORBIDDEN if k in s]
  if hits:raise RuntimeError('PUBLIC_PRIVATE_LEAK:'+','.join(hits))
 for name in FILES:
  b=manifest_bytes if name=='manifest.json' else read_bytes(name)
  observed=next(x for x in inventory if x['name']==name)
  if len(b)!=observed['bytes']:raise RuntimeError('READ_SIZE_CHANGED')
  h=digest(b)
  if name!='manifest.json' and (h!=m['files'][name]['sha256'] or len(b)!=m['files'][name]['bytes']):raise RuntimeError('FILE_SHA_OR_SIZE:'+name)
  results[name]={'bytes':len(b),'sha256':h,'pass':True}
  if name.endswith('.json'):scan(json.loads(b));continue
  if b[:4]!=b'PAR1' or b[-4:]!=b'PAR1' or not 0<struct.unpack('<I',b[-8:-4])[0]<len(b)-8:raise RuntimeError('PARQUET_MAGIC_OR_FOOTER')
  pf=pq.ParquetFile(pa.BufferReader(b));parquet_bytes+=len(b)
  if pf.metadata.num_rows!=COUNTS[name]:raise RuntimeError('ROW_COUNT:'+name)
  schema[name]=[{'name':f.name,'type':str(f.type),'nullable':f.nullable} for f in pf.schema_arrow]
  if name.removesuffix('.parquet') in m['schemas'] and str(pf.schema_arrow)!=m['schemas'][name.removesuffix('.parquet')]:raise RuntimeError('MANIFEST_SCHEMA:'+name)
  count=0;mn=None;mx=None;bins=[0]*60
  for batch in pf.iter_batches(batch_size=4096):
   cols={n:(batch.column(i).cast(pa.int64()).to_pylist() if pa.types.is_timestamp(batch.column(i).type) else batch.column(i).to_pylist()) for i,n in enumerate(batch.schema.names)}
   for f in batch.schema:
    if pa.types.is_timestamp(f.type) and (f.type.unit!='ns' or f.type.tz!='UTC'):raise RuntimeError('TIMESTAMP_SCHEMA')
   for i in range(batch.num_rows):
    row={k:v[i] for k,v in cols.items()};scan(row);count+=1
    t=row['timestamp'] if 'timestamp' in row else row['start_time']
    if not LO<=t<HI:raise RuntimeError('HALF_OPEN_WINDOW:'+name)
    mn=t if mn is None else min(mn,t);mx=t if mx is None else max(mx,t);bins[(t-LO)//(60*10**9)]+=1
    if name=='traces.parquet':
     tid=row['trace_id'];sid=row['span_id'];parent=row['parent_span_id']
     if not re.fullmatch(r'(?:[a-fA-F0-9]{16}|[a-fA-F0-9]{32})',tid) or not re.fullmatch(r'[a-fA-F0-9]{16}',sid) or (parent is not None and not re.fullmatch(r'[a-fA-F0-9]{16}',parent)):raise RuntimeError('TRACE_SPAN_ID')
     trace_counts[tid]=trace_counts.get(tid,0)+1
    elif name=='trace_index.parquet':
     if row['trace_id'] in index_counts or row['end_time']<row['start_time']:raise RuntimeError('TRACE_INDEX')
     index_counts[row['trace_id']]=row['span_count']
  if count!=COUNTS[name]:raise RuntimeError('FULL_DECODE_COUNT')
  results[name].update(rows=count,row_groups=pf.num_row_groups,header_footer='PASS',full_decode='PASS')
  bounds[name]=[mn,mx];minute[name]=bins
  if name!='trace_index.parquet' and bins!=[m['minute_counts'][name.removesuffix('.parquet')][str(i)] for i in range(60)]:raise RuntimeError('MINUTE_COUNTS')
 if trace_counts!=index_counts:raise RuntimeError('TRACE_INDEX_IDENTITY_RECONCILIATION')
 return {'status':'PUBLIC_PACKAGE_VALIDATED','at_utc':datetime.now(timezone.utc).isoformat(),'files':results,'file_count':6,'total_bytes':TOTAL,
  'manifest_sha256':EXPECTED_SHA,'schemas':schema,'timestamp_bounds_ns':bounds,'minute_counts':minute,'public_leak_scan':'PASS_FULL_CONTENT',
  'private_files_present':False,'trace_index_reconciliation':'PASS','pyarrow_version':pa.__version__,'elapsed_seconds':time.monotonic()-started,
  'window':m['window'],'no_database_opened':True}

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--ssh-host');p.add_argument('--ssh-socket');p.add_argument('--output');a=p.parse_args()
 root=Path(a.root)
 if a.ssh_host:
  import shlex
  def ssh(args):return subprocess.check_output(['ssh','-o','BatchMode=yes','-S',a.ssh_socket,a.ssh_host,shlex.join(args)])
  code="import pathlib,stat,json; p=pathlib.Path("+repr(str(root))+"); print(json.dumps([{'name':f.name,'bytes':f.lstat().st_size,'regular':stat.S_ISREG(f.lstat().st_mode),'symlink':f.is_symlink()} for f in p.iterdir()]))"
  inventory=json.loads(ssh(['python3','-c',code]))
  def read_bytes(n):
   if n not in FILES:raise RuntimeError('NOT_WHITE_LISTED')
   return ssh(['cat','--',str(root/n)])
 else:
  inventory=[{'name':f.name,'bytes':f.lstat().st_size,'regular':stat.S_ISREG(f.lstat().st_mode),'symlink':f.is_symlink()} for f in root.iterdir()]
  read_bytes=lambda n:(root/n).read_bytes()
 result=validate(read_bytes,inventory);result.update(source_root=str(root),read_transport='SSH_MEMORY_ONLY_NO_LOCAL_DATA_COPY' if a.ssh_host else 'LOCAL_READONLY')
 if a.output:
  with Path(a.output).open('x') as f:json.dump(result,f,indent=2)
 else:print(json.dumps(result))
 if a.output:print(json.dumps({k:result[k] for k in ('status','file_count','total_bytes','manifest_sha256','public_leak_scan','elapsed_seconds')}))
if __name__=='__main__':main()
