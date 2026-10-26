"""Read-only capability inventory. Explicit file allowlist excludes all Stage5 TEST assets."""
import pathlib,json,subprocess,hashlib,shlex,time,concurrent.futures
from datetime import datetime,timezone
LOCAL=pathlib.Path(__file__).resolve().parents[1]
HOSTS={'master':'192.168.53.52','w1':'192.168.53.53','w2':'192.168.53.54','w3':'192.168.53.55','router':'192.168.53.50'}
def remote(h,args):
 r=subprocess.run(['ssh','-S','/tmp/sfgen-'+h+'.sock','-o','BatchMode=yes','root@'+HOSTS[h],shlex.join(args)],capture_output=True,text=True,timeout=40)
 if r.returncode:raise RuntimeError(h+':'+r.stderr[-500:])
 return r.stdout

def host(h):
 code='''import pathlib,subprocess,json,shutil,socket,os
h=socket.gethostname();data='/data/stage5-v3' if h=='k8s-master' else '/data'
def cmd(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=15);return {'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
mem={k:int(v.split()[0])*1024 for k,v in (l.split(':',1) for l in pathlib.Path('/proc/meminfo').read_text().splitlines())}
print(json.dumps({'hostname':h,'cpus':os.cpu_count(),'root_disk':shutil.disk_usage('/')._asdict(),'data_disk':shutil.disk_usage(data)._asdict() if pathlib.Path(data).exists() else None,'memory_available':mem['MemAvailable'],'clock':cmd(['timedatectl','show','-p','NTPSynchronized']),'timers':cmd(['systemctl','list-timers','--all','--no-pager']),'services':cmd(['systemctl','list-units','--type=service','--state=running','--no-pager']),'qdisc':cmd(['tc','-j','qdisc','show']),'processes':cmd(['ps','-eo','pid,stat,comm'])}))'''
 return h,json.loads(remote(h,['python3','-B','-c',code]))

FILES=[
'/root/ops-competition-2026/attempt04-20261014/plans/SF-PILOT-01-ATTEMPT-04/run-manifest.json',
'/root/ops-competition-2026/attempt04-20261014/plans/SF-PILOT-01-ATTEMPT-04/run-plan.json',
'/root/ops-competition-2026/attempt04-20261014/scripts/run_registry.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/pilot_controller.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/pilot_observer.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/fault_worker.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/pilot_assess.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/pilot_export.py',
'/root/ops-competition-2026/attempt04-20261014/scripts/storage_budget.py',
'/root/ops-competition-2026/scripts/competition_adapter.py',
'/root/ops-competition-2026/reports/SF-PILOT-01-attempt04-report.md',
'/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/capture-and-effects-assessment.json',
'/data/stage5-v3/ops-competition-2026/runtime/SF-PILOT-01-ATTEMPT-04/controller-terminal.json',
'/root/stage4f-cross-layer-validation/scripts/pod_cpu_exec.py',
'/root/stage4f-cross-layer-validation/scripts/host_fault.py',
'/root/stage5-v3-mechanism-qualification/scripts/resource_fault.py',
'/root/cross-layer-rehearsal-phase4/scripts/network_fault.py',
'/root/cross-layer-fault-rehearsal/scripts/network_fault.py',
'/root/stage5-v3-final-fast-ready/results/mechanism-readiness.json',
'/root/stage5-v3-final-fast-ready/reports/four-layer-readiness.md',
'/root/cross-layer-rehearsal-phase4/reports/cross-layer-rehearsal-phase4.md']

def main():
 out=LOCAL/'evidence';out.mkdir(exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:hosts=dict(pool.map(host,HOSTS))
 def save(name,v):
  with (out/name).open('x') as f:json.dump(v,f,indent=2)
 save('hosts-initial.json',{'at_utc':datetime.now(timezone.utc).isoformat(),'hosts':hosts})
 k=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf']
 for kind in ['nodes','pods','services','endpointslices','deployments','configmaps']:
  args=k+(['-n','otel-demo'] if kind!='nodes' else [])+['get',kind,'-o','json']
  save('kubernetes-'+kind+'.json',json.loads(remote('master',args)))
 save('crds.json',json.loads(remote('master',k+['get','crds','-o','json'])))
 code='import pathlib,json,hashlib; paths='+repr(FILES)+'; print(json.dumps([{\"path\":s,\"exists\":pathlib.Path(s).is_file(),\"sha256\":hashlib.sha256(pathlib.Path(s).read_bytes()).hexdigest() if pathlib.Path(s).is_file() else None,\"text\":pathlib.Path(s).read_text() if pathlib.Path(s).is_file() else None} for s in paths]))'
 save('source-reference-inventory.json',json.loads(remote('master',['python3','-B','-c',code])))
 # Ordinary current HTTP evidence; no fault or private metadata query.
 http='''import urllib.request,json,time
end=int(time.time()*1e6);q={'size':6,'query':{'bool':{'filter':[{'term':{'process.serviceName':'frontend-proxy'}},{'range':{'startTime':{'gte':end-120000000,'lt':end}}}]}},'sort':[{'startTime':'desc'}]}
r=urllib.request.Request('http://10.111.114.255:9200/jaeger-jaeger-span-*/_search',data=json.dumps(q).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(r,timeout=15) as f:print(f.read().decode())'''
 save('current-http-trace-evidence.json',json.loads(remote('master',['python3','-B','-c',http])))
 print(json.dumps({'read_only_inventory':'COMPLETE','hosts':5,'reference_files':len(FILES),'faults_injected':0,'test_assets_in_inventory':False}))
if __name__=='__main__':main()
