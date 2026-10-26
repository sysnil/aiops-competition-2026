"""Current pinned cleanup callback availability with EMPTY state; no injection/selftest repeat."""
import importlib.util,json,socket,time,subprocess
from pathlib import Path
from datetime import datetime,timezone
from fault_worker import ADAPTER,ADAPTER_SHA
from run_registry import sha

def main():
    if sha(ADAPTER)!=ADAPTER_SHA:raise RuntimeError('ADAPTER_HASH')
    host=socket.gethostname()
    if host not in ('k8s-worker2','k8s-worker3'):raise RuntimeError('HOST')
    spec=importlib.util.spec_from_file_location('pinned',ADAPTER);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    ident='sf002-cleanup-reachability-'+host;state=m.RUNTIME/(ident+'.json');out=Path('/root/ops-competition-2026/sf002-production-20261014/results')/(ident+'.json');out.parent.mkdir(parents=True,exist_ok=True)
    if state.exists() or out.exists():raise RuntimeError('IMMUTABLE_PROOF_ALREADY_EXISTS')
    m.save(state,{'id':ident,'host':host,'layer':'SELFTEST','status':'ARMED','stopped_targets':[],'children':[]})
    unit='ops-'+ident;v={'status':'PINNED_CLEANUP_TIMER_REACHABILITY_FAILED','business_faults_injected':0,'not_full_mechanism_selftest':True,'adapter_sha256':ADAPTER_SHA,'at_utc':datetime.now(timezone.utc).isoformat()}
    try:
        m.arm(state,3,unit)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            current=m.read(state)
            if current['status']=='CLEANED':break
            time.sleep(.1)
        else:raise RuntimeError('CLEANUP_CALLBACK_NOT_EXECUTED')
        if current['cleanup_result']['continued_pids'] or current['cleanup_result']['terminated_owned_child_pids']:raise RuntimeError('NONEMPTY_SCOPE')
        v.update(status='PINNED_CLEANUP_TIMER_REACHABILITY_PASS',state_sha256=sha(state),cleanup=current['cleanup_result'],timer_unit=unit)
    finally:
        m.stop_timer(unit);v['journal']=subprocess.check_output(['journalctl','-u',unit+'.service','--no-pager','-n','15'],text=True);m.save(out,v)
    print(json.dumps(v))
if __name__=='__main__':main()
