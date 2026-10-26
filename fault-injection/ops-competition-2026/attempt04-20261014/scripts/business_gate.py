"""Shared baseline and post-action observations; never claims actual fault recovery."""
import argparse,sys,json,time,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
from run_registry import load_bundle,action_for,sha
from pilot_common import RUN,CONTROL,MANIFEST_PATH,MANIFEST_SHA
from handshake import atomic
sys.path.insert(0,'/root/ops-competition-2026/scripts')
from competition_observation import OLD,CG,safety
from competition_qualification import span_error
OLD=CG=Path('/root/ops-competition-2026/attempt04-20261014/capture')

def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--action-id',required=True);p.add_argument('--phase',choices=['BASELINE','RECOVERY'],required=True);a=p.parse_args()
    m,plan=load_bundle(a.manifest,a.manifest_sha);action=action_for(m,a.action_id)
    if (a.manifest,a.manifest_sha)!=(MANIFEST_PATH,MANIFEST_SHA):raise RuntimeError('GATE_REGISTRY_DIVERGENCE')
    q=json.loads((RUN/'capture/chunk-00/quality.json').read_text())
    if q['status']!='CHUNK_QUALITY_PASS':raise RuntimeError('FIRST_CAPTURE_AUDIT_FAILED')
    end=datetime.fromisoformat(action['planned_utc'])-timedelta(seconds=60) if a.phase=='BASELINE' else datetime.now(timezone.utc)-timedelta(seconds=15)
    start=end-timedelta(seconds=60)
    if not 0<=time.time()-end.timestamp()<=120:raise RuntimeError('BUSINESS_GATE_NOT_FRESH')
    out=RUN/'stage-evidence'/action['action_id']/a.phase.lower()
    cmd=['python3','-B',str(OLD/'capture_live_c2.py'),'--phase','before','--start-utc',start.isoformat(),'--end-utc',end.isoformat(),'--trace-source','JAEGER_OPENSEARCH_STORAGE','--output',str(out)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=65)
    if r.returncode:raise RuntimeError('BUSINESS_CAPTURE_FAILED:'+r.stderr[-500:])
    sys.path.insert(0,str(CG));from trace_identity import span_map
    counts={t['uid']:set() for t in plan['targets_and_siblings']};errors=[]
    for _,s in span_map(out).values():
        if s.get('tag',{}).get('span@kind')!='server':continue
        uid={x.get('key'):x.get('value') for x in s['process'].get('tags',[])}.get('k8s.pod.uid')
        if uid in counts:counts[uid].add(s['traceID'])
        if s['process']['serviceName']=='frontend-proxy' and span_error(s):errors.append(s['traceID'])
    wanted=[t for t in plan['targets_and_siblings'] if t['service']==action['targets'][0]['service']]
    checks={'target_and_sibling_min_10_traces':all(len(counts[t['uid']])>=10 for t in wanted),'no_ingress_errors':not errors,'logs_present':json.loads((out/'capture-manifest.json').read_text())['log_records']>0,'metrics_present':(out/'metrics/memory.json').exists()}
    env=safety(plan['targets_and_siblings'])
    result={'status':'BUSINESS_GATE_PASS' if all(checks.values()) else 'BUSINESS_GATE_FAIL','phase':a.phase,'run_id':plan['id'],'action_id':a.action_id,'at_utc':datetime.now(timezone.utc).isoformat(),'manifest_sha256':a.manifest_sha,'checks':checks,'uid_trace_counts':{k:len(v) for k,v in counts.items()},'actual_fault_recovery_claimed':False,'public_observation_only':True,'environment':env,'capture_manifest_sha256':sha(out/'capture-manifest.json')}
    atomic(CONTROL/(a.action_id+'-'+a.phase.lower()+'.json'),result,True);print(json.dumps(result))
    if not all(checks.values()):raise SystemExit(1)
if __name__=='__main__':main()
