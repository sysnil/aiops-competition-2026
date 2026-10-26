"""Master-owned control transport; no remote credentials in files."""
import json,subprocess,shlex,time
from datetime import datetime,timezone
from pathlib import Path
BASE=Path('/root/ops-competition-2026/sf007-real-adapter-v4-20261017')
HOSTS={'w3':'192.168.53.55'}
def now():return datetime.now(timezone.utc).isoformat()
def ssh(host,args):return ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=3','-S','/run/ops-sf007-ssh/'+host+'.sock','root@'+HOSTS[host],shlex.join(list(map(str,args)))]
def remote(host,args,timeout=30):
    r=subprocess.run(ssh(host,args),capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError(host+': '+r.stdout[-500:]+r.stderr[-1000:])
    return r.stdout
def put(host,path,value,replace=False):
    body=json.dumps(value)
    code='import pathlib,os,json; p=pathlib.Path('+repr(str(path))+'); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); '
    if replace:code+='t=p.with_suffix(".tmp"); t.write_text('+repr(body)+'); t.chmod(0o600); os.replace(t,p)'
    else:code+='f=p.open("x"); f.write('+repr(body)+'); f.close(); p.chmod(0o600)'
    remote(host,['python3','-c',code],10)
def optional(host,path):
    return json.loads(remote(host,['python3','-c','import pathlib; p=pathlib.Path('+repr(str(path))+'); print(p.read_text() if p.is_file() else "null")'],10))
def copy(host,source,destination,reverse=False,recursive=False):
    args=['scp','-p','-o','BatchMode=yes','-o','ControlPath=/run/ops-sf007-ssh/'+host+'.sock']
    if recursive:args.append('-r')
    args+=['root@'+HOSTS[host]+':'+str(source),str(destination)] if reverse else [str(source),'root@'+HOSTS[host]+':'+str(destination)]
    subprocess.run(args,check=True,capture_output=True,text=True,timeout=60)
def clock(host,n=5):
    rows=[]
    for _ in range(n):
        a=time.time();m=time.monotonic()
        v=json.loads(remote(host,['python3','-c','import time,json; print(json.dumps({"epoch":time.time()}))'],5))
        dt=time.monotonic()-m;b=time.time()
        if abs((b-a)-dt)>.01:raise RuntimeError('MASTER_CLOCK_STEP')
        rows.append({'worker_minus_master_seconds':v['epoch']-(a+b)/2,'uncertainty_seconds':dt/2+.002,'rtt_seconds':dt})
    best=min(rows,key=lambda r:r['uncertainty_seconds'])
    return {**best,'measured_at_master_utc':now(),'samples':rows}
def lease(host,plan,digest,clockproof,safety=True):
    issue=time.time();v={'plan_sha256':digest,'authorized':True,'issued_utc':now(),
        'expires_utc':datetime.fromtimestamp(issue+4,timezone.utc).isoformat(),'clock':clockproof,'safety_pass':safety}
    put(host,Path(plan['runtime'])/'lease.json',v,True)
