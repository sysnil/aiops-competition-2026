"""Read-only complete workflow/capture/negative-test acceptance; no RCA or GT."""
import json,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path('/root/ops-competition-2026/pre-attempt04-20261014');DATA=Path('/data/stage5-v3/ops-competition-2026/pre-attempt04')
sys.path.insert(0,str(ROOT/'scripts'))
from run_registry import load_bundle,sha,stamp
from pilot_common import boundary_match
from handshake import digest
def read(p):return json.loads(Path(p).read_text())
def main():
    run=sys.argv[1];mp=ROOT/'plans'/run/'run-manifest.json';m,p=load_bundle(mp,sha(mp));r=Path(p['raw_root']);c=Path(p['control_root']);t=read(c/'controller-terminal.json')
    checks={'workflow_complete':t['status']=='DRY_RUN_WORKFLOW_COMPLETE','no_business_faults':t['business_faults_injected']==0,'not_formal':p['formal_case'] is False,'source_versions_unchanged':all(sha(f)==h for f,h in p['source_sha256'].items()),'two_actions_cancelled':len(t.get('cancelled',[]))==2,'final_environment':not read(t['post_health'])['current_environment_blockers']}
    stages={}
    for i,a in enumerate(m['actions']):
        ident=a['action_id'];s=t['stages'].get(ident,{});onset=t['onsets'].get(ident,{})
        pre=read(Path('/data/stage5-v3/ops-competition-2026/audits')/('COMP-SF01-'+run+'-'+ident+'-PRE')/'phase-a-decision.json')
        baseline=read(s['baseline']);recovery=read(s['simulated_recovery']);worker=c/(ident+'-worker-final');cancel=read(worker/'cancelled.json');auth=read(c/(ident+'-authorization.json'))
        good={'prefault_precheck_pass':not pre['current_environment_blockers'],'exact_pending_set':pre['required_pending_actions']==[x['action_id'] for x in m['actions'][i:]],'same_manifest_preflight':pre['registry_manifest_sha256']==sha(mp),'baseline_pass':baseline['status']=='BUSINESS_GATE_PASS','same_manifest_authorization':auth['manifest_sha256']==sha(mp) and auth['permission']=='MARKER_ONLY','marker_complete':s['execution']['status']=='MARKER_COMPLETE','duration_bound':10<=s['execution']['duration_seconds']<=12,'onset_and_feedback':onset.get('pass') is True and onset['feedback_upper_seconds']<=2,'cancelled_and_revoked':cancel['inactive'] and cancel['lease_revoked'],'rearm_rejected':s.get('rearm_rejected') is True,'simulated_recovery_pass':recovery['status']=='BUSINESS_GATE_PASS' and recovery['actual_fault_recovery_claimed'] is False}
        checks[ident]=all(good.values());stages[ident]={'checks':good,'onset':onset,'duration_seconds':s['execution']['duration_seconds'],'baseline_uid_counts':baseline['uid_trace_counts'],'recovery_uid_counts':recovery['uid_trace_counts']}
    heartbeats=[read(f) for f in sorted((c/'heartbeats').glob('*.json'))]
    checks['heartbeat_identity_sequence']=all(h['sequence']==i and h['run_id']==run and h['run_token']==p['run_token'] and h['plan_sha256']==m['plan_sha256'] and h['checks_sha256']==digest(h['checks']) for i,h in enumerate(heartbeats))
    maxgap=max(stamp(b['completed_utc'])-stamp(a['completed_utc']) for a,b in zip(heartbeats,heartbeats[1:]));checks['heartbeat_continuity']=maxgap<=30
    samples=[json.loads(l) for l in (r/'safety.jsonl').read_text().splitlines()];checks['collector_refused_no_increment']=all(s['collector']['refused']=={'grpc':0.0,'http':0.0} for s in samples)
    chunks={};proxies=set()
    for f in sorted((r/'capture').glob('*/quality.json')):
        q=read(f);proxies.update(q.get('proxy_trace_ids',[]));chunks[f.parent.name]={k:v for k,v in q.items() if k!='proxy_trace_ids'}
    checks['all_three_chunks_complete']=len(chunks)==3 and all(q['status']=='CHUNK_QUALITY_PASS' for q in chunks.values())
    requests=[]
    for rel in ('state-machine/request-events-live.jsonl','controls/control-events-live.jsonl'):requests.extend(json.loads(l) for l in (r/rel).read_text().splitlines())
    start=stamp(p['start_utc']);end=start+900;eligible=[x for x in requests if start<=stamp(x['request_started_utc'])<end]
    guards=[]
    for side in ('start','end'):
        for f in (r/'private-boundary'/side/'trace-storage-pages').glob('*.json'):guards.extend(h['_source'] for h in read(f)['hits']['hits'])
    missing=[x for x in eligible if x['trace_id'].lower() not in proxies and not any(boundary_match(x,s,start,end) for s in guards)]
    checks['all_requests_successful']=all(x['success'] for x in eligible);checks['all_request_ids_matched']=not missing
    controls=read(r/'controls/result-manifest.json')['streams'];checks['full_rate_controls']=all(controls[name]['successful_requests']==controls[name]['planned_requests']==900*rate and controls[name]['missed_arrivals']==0 for name,rate in [('image_asset',1),('cart_view',2)])
    leases=[json.loads(l) for l in (c/'leases.jsonl').read_text().splitlines()]
    checks['lease_scope_and_duration']=all(l['permission']=='MARKER_ONLY' and l['manifest_sha256']==sha(mp) and stamp(l['expires_utc'])-stamp(l['issued_utc'])<=4.01 for l in leases)
    neg=sys.argv[2];nc=DATA/'runtime'/neg;nt=read(nc/'controller-terminal.json');nd=read(Path('/data/stage5-v3/ops-competition-2026/audits')/('COMP-SF01-'+neg+'-ARMED')/'phase-a-decision.json')
    checks['negative_hash_rejected']=nt['status']=='DRY_RUN_FAILED' and 'run_registry_valid' in nd['current_environment_blockers'] and nd['actual_metric_source_timestamps'].get('registry_error')=='REGISTRATION_FILE_HASH'
    checks['negative_no_execution_and_cleaned']=not nt['stages'] and not nt['onsets'] and len(nt.get('cancelled',[]))==2 and not (nc/'leases.jsonl').exists() and not read(nt['post_health'])['current_environment_blockers']
    value={'status':'DRY_RUN_ENGINEERING_PASS' if all(checks.values()) else 'DRY_RUN_ENGINEERING_FAILED','checks':checks,'failed':[k for k,v in checks.items() if not v],'stages':stages,'heartbeat_count':len(heartbeats),'max_heartbeat_gap_seconds':maxgap,'safety_samples':len(samples),'collector_peak_bytes':max(s['collector']['working_set_bytes'] for s in samples),'requests':len(eligible),'unmatched_requests':missing,'chunks':chunks,'lease_records':len(leases),'formal_case_created':False,'actual_fault_recovery_claimed':False,'at_utc':datetime.now(timezone.utc).isoformat()}
    out=ROOT/'results'/(run+'-assessment.json')
    with out.open('x') as f:json.dump(value,f,indent=2)
    print(json.dumps(value))
    if value['failed']:raise SystemExit(1)
if __name__=='__main__':main()
