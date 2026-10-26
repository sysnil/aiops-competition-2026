"""Single authorized ATTEMPT04 registration; no scheduling/injection occurs here."""
import argparse,json,time,secrets
from pathlib import Path
from datetime import datetime,timezone
from run_registry import ROOT,BACKEND,sha,load_bundle
from handshake import atomic
from storage_budget import budget
RUN='SF-PILOT-02-ATTEMPT-01'
def read(p):return json.loads(Path(p).read_text())
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
def main():
    q=argparse.ArgumentParser();q.add_argument('--health',required=True);q.add_argument('--inspection',required=True);q.add_argument('--seed',required=True,type=int);a=q.parse_args()
    health=read(a.health);inspection=read(a.inspection)
    if health['current_environment_blockers'] or health['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or not 0<=time.time()-datetime.fromisoformat(health['at_utc']).timestamp()<=120:raise RuntimeError('FRESH_SAFETY_REQUIRED')
    if not 0<=time.time()-datetime.fromisoformat(inspection['at_utc']).timestamp()<=300:raise RuntimeError('FRESH_IDENTITY_REQUIRED')
    prior=Path('/root/ops-competition-2026/pre-attempt04-20261014');ready=read(prior/'results/readiness-decision.json')
    if ready.get('status')!='PRE_ATTEMPT04_READY':raise RuntimeError('AUTHORITATIVE_NOT_READY')
    full=read(prior/'plans/SFDRY-FULL-02/run-plan.json')
    if any(sha(f)!=v for f,v in full['source_sha256'].items()):raise RuntimeError('VALIDATED_SOURCE_CHANGED')
    old=read('/root/ops-competition-2026/plans/SF-PILOT-01-ATTEMPT-03.json');current=inspection['targets']
    for t in old['targets_and_siblings']:
        if not any(all(r[k]==t[k] for k in ('uid','container_id','node','restart_count')) for r in current):raise RuntimeError('CURRENT_TARGET_CHANGED_REQUALIFICATION_REQUIRED')
    for layer in ('SERVICE','POD'):
        evidence=read('/root/ops-competition-2026/qualification/COMP-SF01-'+layer+'-300/decision.json')
        if evidence.get('qualified') is not True or evidence.get('blockers'):raise RuntimeError('HISTORICAL_300S_QUALIFICATION_REQUIRED')
    capacity=budget()
    if capacity['status']!='STORAGE_PEAK_BUDGET_PASS':raise RuntimeError('PEAK_STORAGE_BUDGET:'+json.dumps(capacity))
    folder=ROOT/'plans'/RUN
    if folder.exists() or Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002').exists():raise RuntimeError('ONE_ATTEMPT_NO_OVERWRITE')
    folder.mkdir(parents=True,mode=0o700);planpath=folder/'run-plan.json';start=time.time()+180
    sources={str(f):sha(f) for d in ('scripts','capture') for f in (ROOT/d).glob('*.py')}
    sources.update({str(f):sha(f) for f in (ROOT/'design').glob('*.json')})
    sources.update(read('/root/ops-competition-2026/plans/competition-source-lock-v1.json')['source_sha256'])
    if any(sha(f)!=v for f,v in sources.items()):raise RuntimeError('PINNED_SOURCES_CHANGED')
    actions=[]
    for layer,worker,offset in [('SERVICE','k8s-worker3',720),('POD','k8s-worker2',2100)]:
        qual=read('/root/ops-competition-2026/plans/COMP-SF01-'+layer+'-300.json');targets=[]
        for t in qual['targets']:
            live=next(r for r in current if r['uid']==t['uid'])
            if len(live['processes'])!=1 or any(str(live['processes'][0][k])!=str(t[k]) for k in ('pid','start_ticks')) or live['cgroup']!=t['cgroup']:raise RuntimeError('PROCESS_CHANGED')
            if layer=='POD' and not live['conservative_32MiB_plus_32MiB_overhead_fits_80pct']:raise RuntimeError('POD_HEADROOM')
            targets.append(t)
        ident=RUN+'-'+layer;registration=folder/(ident+'-qualified-registration.json')
        reg={**qual,'id':ident,'targets':targets,'prerequisites':old['prerequisites'],'at_utc':utc(time.time())};atomic(registration,reg,True)
        wd=ROOT/'results'/('sf002-cleanup-reachability-'+worker+'.json');proof=read(wd)
        if proof['status']!='PINNED_CLEANUP_TIMER_REACHABILITY_PASS' or not 0<=time.time()-datetime.fromisoformat(proof['at_utc']).timestamp()<=3600:raise RuntimeError('CURRENT_WATCHDOG_CALLBACK')
        historical=Path('/root/ops-competition-2026/qualification')/('watchdog-v3-'+worker+'.json')
        actions.append({'run_id':RUN,'action_id':ident,'fault_event_id':'SF-002-E01' if layer=='SERVICE' else 'SF-002-E02','fault_category':'Code' if layer=='SERVICE' else 'Resource','fault_type':'ServicePause' if layer=='SERVICE' else 'PodMemoryStress','target_layer':layer,'mechanism_id':layer,'worker':worker,'targets':targets,'allocation_bytes':33554432,
          'qualified_registration':str(registration),'qualified_registration_sha256':sha(registration),'injection_output':'/root/ops-competition-2026/qualification/private-attempts/'+ident,
          'watchdog_proof_file':str(historical),'watchdog_proof_sha256':sha(historical),'watchdog_reachability_file':str(wd),'watchdog_reachability_sha256':sha(wd),
          'planned_utc':utc(start+offset),'offset_seconds':offset,'duration_seconds':300,'recovery_offset_seconds':old['recovery_check_offsets_seconds'][layer],
          'unit':'ops-sf002-'+ident.lower(),'runtime':str(ROOT/'worker-runtime'/ident),'registration_file':str(planpath),
          'backend':BACKEND,'execute':'RUN_PINNED_QUALIFIED_ADAPTER','revoke':'REVOKE_STOP_AND_PINNED_CLEANUP','onset_tolerance_seconds':2})
    data=Path('/data/stage5-v3/ops-competition-2026')
    plan={**old,'public_case_id':'SF-002','storage_policy_sha256':sha(ROOT/'design/storage-policy.json'),'id':RUN,'run_token':secrets.token_hex(24),'backend':BACKEND,'formal_case':True,'authorization_scope':'ONE_SF002_SERVICE_POD_QUALIFIED_ONLY',
      'actions':actions,'faults':[{'id':r['action_id'],'offset_seconds':r['offset_seconds'],'duration_seconds':300,'plan':r['qualified_registration'],'sha256':r['qualified_registration_sha256']} for r in actions],
      'markers':[],'source_sha256':sources,'duration_seconds':3600,'start_utc':utc(start),'raw_root':str(data/'batch-sf002-sf009/SF-002'),'control_root':str(data/'batch-sf002-sf009/runtime'/RUN),
      'controller_unit':'ops-sf002-controller-'+RUN.lower(),'observer_unit':'ops-sf002-observer-'+RUN.lower(),'health_file':a.health,'health_sha256':sha(a.health),'seed':a.seed,
      'identity_file':a.inspection,'identity_sha256':sha(a.inspection),'storage_budget':capacity,'initialization_budget_seconds':15,'heartbeat_freshness_seconds':30,
      'authorization_reference':'User authorized SF-002 as part of eight-case batch; one attempt; only verified 300s Service and 32MiB/300s Pod mechanisms',
      'validated_basis_manifest_sha256':sha(prior/'plans/SFDRY-FULL-02/run-manifest.json'),'validated_readiness_sha256':sha(prior/'results/readiness-decision.json')}
    atomic(planpath,plan,True)
    lockpath=folder/'schedule-lock.json';atomic(lockpath,{'run_id':RUN,'plan_sha256':sha(planpath),'actions':actions},True)
    manifest={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':RUN,'backend':BACKEND,'plan_path':str(planpath),'plan_sha256':sha(planpath),'schedule_lock_path':str(lockpath),'schedule_lock_sha256':sha(lockpath),'actions':actions,'controller_unit':plan['controller_unit'],'observer_unit':plan['observer_unit']}
    path=folder/'run-manifest.json';atomic(path,manifest,True);load_bundle(path,sha(path))
    print(json.dumps({'run_id':RUN,'manifest':str(path),'manifest_sha256':sha(path),'start_utc':plan['start_utc'],'fresh_safety':'SF002_FRESH_SAFETY_PASS','actions':[{'id':x['action_id'],'utc':x['planned_utc']} for x in actions]}))
if __name__=='__main__':main()
