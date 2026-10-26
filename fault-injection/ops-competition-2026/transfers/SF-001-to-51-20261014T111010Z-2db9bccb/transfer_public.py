"""Whitelist-only encrypted SSH relay. No public data is written on the relay machine."""
import subprocess,pathlib,json,hashlib,time,shlex,uuid
from datetime import datetime,timezone
LOCAL=pathlib.Path(__file__).resolve().parent
SOURCE='/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/public'
FILES=['metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet','topology.json','manifest.json']
ENDPOINTS={'source':('root@192.168.53.52','/tmp/sfgen-master.sock'),'target':('root@192.168.51.51','/tmp/sf001-transfer-51.sock')}
def now():return datetime.now(timezone.utc).isoformat()
def command(host,args):
 user,sock=ENDPOINTS[host]
 return ['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','-S',sock,user,shlex.join(list(map(str,args)))]
def run(host,args):return subprocess.check_output(command(host,args),timeout=30,text=True)
validation=json.loads((LOCAL/'source-validation.json').read_text());before=json.loads((LOCAL/'target-before.json').read_text())
assert validation['status']=='PUBLIC_PACKAGE_VALIDATED' and validation['total_bytes']==77945722 and set(validation['files'])==set(FILES)
assert before['status']=='TARGET_READONLY_LAYOUT_AND_ACCESS_PASS' and not before['final_exists']
ident=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
parent='/data/aiops-contest/staging';temp=parent+'/.SF-001-incoming-'+ident+'.partial';final=parent+'/SF-001-incoming'
plan={'transfer_id':ident,'source':SOURCE,'target_host':'192.168.51.51','temporary_directory':temp,'final_directory':final,'files':validation['files'],
 'method':'TWO_ENCRYPTED_SSH_STREAMS_VIA_MAC_MEMORY_PIPE','local_public_files_written':False,'source_parent_directory_copied':False,'clock':'relay UTC; elapsed time uses monotonic clock','started_utc':now()}
with (LOCAL/'transfer-plan.json').open('x') as f:json.dump(plan,f,indent=2)
prep="""import pathlib,os,json,subprocess,shutil,stat
p=pathlib.Path(%r);final=pathlib.Path(%r)
m=json.loads(subprocess.check_output(['findmnt','-J','-T',str(p.parent),'-o','TARGET,SOURCE,FSTYPE,UUID,OPTIONS'],text=True))['filesystems'][0]
assert m['target']=='/data' and m['uuid']=='b54b1161-c8ef-43d2-b883-c691824bd84c' and 'rw' in m['options'].split(',')
assert not final.exists() and not final.is_symlink() and not p.exists()
assert p.parent.stat().st_uid==0 and stat.S_IMODE(p.parent.stat().st_mode)==0o700
assert shutil.disk_usage(p.parent).free>2*77945722+1024**3
p.mkdir(mode=0o700);os.chmod(p,0o700)
print(json.dumps({'temporary_directory':str(p),'mode':oct(stat.S_IMODE(p.stat().st_mode)),'disk_free_bytes':shutil.disk_usage(p).free}))
"""%(temp,final)
result={**plan,'status':'SF001_TRANSFER_PARTIAL','files_transferred':[]};began=time.monotonic()
try:
 result['preparation']=json.loads(run('target',['python3','-B','-c',prep]))
 for name in FILES:
  meta=validation['files'][name]
  source_code="import pathlib,hashlib,sys; p=pathlib.Path("+repr(SOURCE+'/'+name)+"); assert p.is_file() and not p.is_symlink(); b=p.read_bytes(); assert len(b)=="+str(meta['bytes'])+" and hashlib.sha256(b).hexdigest()=="+repr(meta['sha256'])+"; sys.stdout.buffer.write(b)"
  receive_code="""import pathlib,os,sys,hashlib,json,stat
from datetime import datetime,timezone
root=pathlib.Path(%r);name=%r;size=%d;expected=%r
assert root.is_dir() and not root.is_symlink() and root.stat().st_uid==0 and stat.S_IMODE(root.stat().st_mode)==0o700
assert root.stat().st_dev==os.stat('/data').st_dev
p=root/name;part=root/(name+'.part');assert not p.exists() and not part.exists()
fd=os.open(part,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);written=0
with os.fdopen(fd,'wb') as f:
 while written<size:
  block=sys.stdin.buffer.read(min(1024*1024,size-written))
  if not block:raise RuntimeError('EARLY_EOF_RETAIN_PARTIAL')
  f.write(block);written+=len(block)
 if sys.stdin.buffer.read(1):raise RuntimeError('EXCESS_BYTES_RETAIN_PARTIAL')
 f.flush();os.fsync(f.fileno())
h=hashlib.sha256(part.read_bytes()).hexdigest()
assert part.stat().st_size==size and h==expected
assert not p.exists();os.rename(part,p)
d=os.open(root,os.O_DIRECTORY);os.fsync(d);os.close(d)
print(json.dumps({'file':name,'bytes':written,'sha256':h,'target_file_check':'PASS','received_utc':datetime.now(timezone.utc).isoformat(),'mode':oct(stat.S_IMODE(p.stat().st_mode))}))
"""%(temp,name,meta['bytes'],meta['sha256'])
  start=time.monotonic();reader=subprocess.Popen(command('source',['python3','-B','-c',source_code]),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  writer=subprocess.Popen(command('target',['python3','-B','-c',receive_code]),stdin=reader.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  reader.stdout.close()
  try:out,err=writer.communicate(timeout=180);_,source_error=reader.communicate(timeout=30)
  except BaseException:
   writer.terminate();reader.terminate();writer.wait(timeout=10);reader.wait(timeout=10);raise
  if writer.returncode or reader.returncode:raise RuntimeError('FILE_TRANSFER_FAILED:'+name+':'+(err+source_error).decode(errors='replace')[-1000:])
  record=json.loads(out);record['elapsed_seconds']=time.monotonic()-start;record['bytes_per_second']=record['bytes']/record['elapsed_seconds'];result['files_transferred'].append(record)
  with (LOCAL/'per-file-transfer.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
  print(json.dumps(record),flush=True)
 result['status']='ALL_SIX_FILES_WRITTEN_PENDING_INDEPENDENT_TARGET_VALIDATION'
except Exception as exc:
 result['error']=type(exc).__name__+': '+str(exc)
 result['status']='SF001_TRANSFER_PARTIAL' if result['files_transferred'] else 'SF001_TRANSFER_FAILED'
finally:
 result['ended_utc']=now();result['elapsed_seconds']=time.monotonic()-began;result['bytes_transferred']=sum(x['bytes'] for x in result['files_transferred']);result['effective_bytes_per_second']=result['bytes_transferred']/result['elapsed_seconds']
 with (LOCAL/'transfer-result.json').open('x') as f:json.dump(result,f,indent=2)
 print(json.dumps({k:result[k] for k in ['status','temporary_directory','bytes_transferred','elapsed_seconds','effective_bytes_per_second']}),flush=True)
if result['status']!='ALL_SIX_FILES_WRITTEN_PENDING_INDEPENDENT_TARGET_VALIDATION':raise SystemExit(1)
