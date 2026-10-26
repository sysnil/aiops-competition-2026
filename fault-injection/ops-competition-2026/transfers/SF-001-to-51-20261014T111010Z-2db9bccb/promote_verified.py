import pathlib,json,subprocess,shlex
LOCAL=pathlib.Path(__file__).resolve().parent
source=json.loads((LOCAL/'source-validation.json').read_text());target=json.loads((LOCAL/'target-validation.json').read_text());plan=json.loads((LOCAL/'transfer-plan.json').read_text())
assert source['files']==target['files'] and source['schemas']==target['schemas'] and source['timestamp_bounds_ns']==target['timestamp_bounds_ns']
assert target['status']=='PUBLIC_PACKAGE_VALIDATED'
code='''import pathlib,json,hashlib,stat,os,ctypes,subprocess,shutil
from datetime import datetime,timezone
p=pathlib.Path(TEMP);final=pathlib.Path(FINAL);expected=EXPECTED
assert p.parent==final.parent and not p.is_symlink() and not final.exists() and not final.is_symlink()
assert set(x.name for x in p.iterdir())==set(expected)
for name,v in expected.items():
 f=p/name;st=f.lstat()
 assert stat.S_ISREG(st.st_mode) and st.st_uid==0 and st.st_gid==0 and stat.S_IMODE(st.st_mode)==0o600
 assert st.st_size==v['bytes'] and hashlib.sha256(f.read_bytes()).hexdigest()==v['sha256']
mount=json.loads(subprocess.check_output(['findmnt','-J','-T',str(p),'-o','TARGET,SOURCE,FSTYPE,UUID,OPTIONS'],text=True))['filesystems'][0]
assert mount['target']=='/data' and mount['uuid']=='b54b1161-c8ef-43d2-b883-c691824bd84c' and p.stat().st_dev==p.parent.stat().st_dev
libc=ctypes.CDLL(None,use_errno=True);rename=libc.renameat2
rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];rename.restype=ctypes.c_int
if rename(-100,os.fsencode(p),-100,os.fsencode(final),1)!=0:raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
fd=os.open(final.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
assert not p.exists() and final.is_dir() and stat.S_IMODE(final.stat().st_mode)==0o700
checks={}
for name,v in expected.items():
 f=final/name
 assert f.stat().st_size==v['bytes'] and hashlib.sha256(f.read_bytes()).hexdigest()==v['sha256']
 checks[name]={'bytes':f.stat().st_size,'sha256':v['sha256'],'mode':oct(stat.S_IMODE(f.stat().st_mode)),'uid':f.stat().st_uid,'gid':f.stat().st_gid}
assert set(x.name for x in final.iterdir())==set(expected)
permissions={}
for user in ('aiops-api','www-data'):
 result=subprocess.run(['runuser','-u',user,'--','test','-r',str(final/'metrics.parquet')],capture_output=True)
 assert result.returncode==1
 permissions[user]='READ_DENIED_AS_EXPECTED'
assert os.access(final/'metrics.parquet',os.R_OK)
print(json.dumps({'status':'SF001_TRANSFER_READY','target_directory':str(final),'at_utc':datetime.now(timezone.utc).isoformat(),'atomic_no_replace':True,'file_count':len(checks),'total_bytes':sum(x['bytes'] for x in checks.values()),'files':checks,'mount':mount,'data_disk_free_bytes':shutil.disk_usage(final).free,'directory_owner':'root:root','directory_mode':'0o700','permissions':permissions,'root_readable':True,'extra_files':[],'temporary_directory_remaining':False}))
'''
code='TEMP='+repr(plan['temporary_directory'])+'\nFINAL='+repr(plan['final_directory'])+'\nEXPECTED='+repr(source['files'])+'\n'+code
r=subprocess.run(['ssh','-S','/tmp/sf001-transfer-51.sock','-o','BatchMode=yes','root@192.168.51.51',shlex.join(['python3','-B','-c',code])],capture_output=True,text=True,check=True,timeout=30)
v=json.loads(r.stdout)
with (LOCAL/'promotion-and-final-inventory.json').open('x') as f:json.dump(v,f,indent=2)
print(json.dumps({k:v[k] for k in ['status','target_directory','file_count','total_bytes','data_disk_free_bytes','permissions','directory_mode','atomic_no_replace']}))
