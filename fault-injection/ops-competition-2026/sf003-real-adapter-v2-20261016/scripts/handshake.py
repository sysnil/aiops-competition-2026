"""Attempt-bound, atomic real-safety handshake; missing never means safe."""
import hashlib,json,os,time
from pathlib import Path
from datetime import datetime,timezone

def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def atomic(p,v,immutable=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=p.with_name(p.name+'.'+str(os.getpid())+'.tmp')
    with tmp.open('x') as f:json.dump(v,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    tmp.chmod(0o600)
    if immutable:os.link(tmp,p);tmp.unlink()
    else:os.replace(tmp,p)

class Gate:
    def __init__(self,run_id,token,plan_sha,producer_sha,started_at,t0,first_budget=15):
        self.identity=(run_id,token,plan_sha,producer_sha)
        self.started_at=started_at;self.t0=t0;self.first_budget=first_budget
        self.sequence=-1;self.previous=None;self.first_received=None;self.state='INITIALIZING'
    def accept(self,h,at,pid,alive=True):
        if not alive:raise RuntimeError('OBSERVER_EXITED')
        if h is None:
            self.state='WAITING_FOR_FIRST_SAMPLE'
            if at>=min(self.started_at+self.first_budget,self.t0):raise RuntimeError('FIRST_HEARTBEAT_DEADLINE')
            return False
        if not isinstance(h,dict):raise RuntimeError('HEARTBEAT_SCHEMA')
        if tuple(h.get(k) for k in ('run_id','run_token','plan_sha256','producer_sha256'))!=self.identity:raise RuntimeError('HEARTBEAT_IDENTITY')
        if type(h.get('sequence')) is not int or h['sequence']<0 or h.get('pid')!=pid:raise RuntimeError('HEARTBEAT_PRODUCER_SEQUENCE')
        try:began,completed=stamp(h['check_started_utc']),stamp(h['completed_utc'])
        except (ValueError,TypeError,KeyError):raise RuntimeError('HEARTBEAT_TIMESTAMP')
        if not self.started_at-1<=began<=completed<=at or at-began>30:raise RuntimeError('HEARTBEAT_STALE_OR_FUTURE')
        if h.get('status')!='LIVE_ENVIRONMENT_PASS' or h.get('checks_sha256')!=digest(h.get('checks')):raise RuntimeError('HEARTBEAT_UNSAFE_OR_CORRUPT')
        seq=h['sequence'];d=digest(h)
        if seq<self.sequence or (seq==self.sequence and d!=self.previous):raise RuntimeError('HEARTBEAT_REPLAY_OR_REWRITE')
        if self.first_received is None:
            if seq!=0 or at>=min(self.started_at+self.first_budget,self.t0):raise RuntimeError('FIRST_HEARTBEAT_DEADLINE_OR_SEQUENCE')
            self.first_received=at
        self.sequence=seq;self.previous=d;self.state='RUNNING' if at>=self.t0 else 'READY'
        return True

def publish(control,plan,plan_sha,producer_sha,seq,check_started,checks):
    h={'run_id':plan['id'],'run_token':plan['run_token'],'plan_sha256':plan_sha,
       'producer_sha256':producer_sha,'pid':os.getpid(),'sequence':seq,'check_started_utc':check_started,
       'completed_utc':datetime.now(timezone.utc).isoformat(),'status':'LIVE_ENVIRONMENT_PASS',
       'checks':checks,'checks_sha256':digest(checks)}
    atomic(Path(control)/'heartbeats'/(f'{seq:06d}.json'),h,True)
    atomic(Path(control)/'heartbeat.json',h)
    return h
