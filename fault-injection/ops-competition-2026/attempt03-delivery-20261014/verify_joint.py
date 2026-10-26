"""Read-only acceptance of the real joint rehearsal and observer-exit cancellation test."""
import json,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/root/ops-competition-2026/attempt03-20261014')
DATA=Path('/data/stage5-v3/ops-competition-2026')
sys.path.insert(0,str(BASE/'scripts'))
from handshake import digest,stamp
from pilot_common import boundary_match
from master_transport import optional,remote,copy
def read(p):return json.loads(Path(p).read_text())
def main():
    findings={};fail=[]
    for ident in ('A03-NOFAULT-INTEGRATION','A03-OBSERVER-EXIT-TEST'):
        run=DATA/'runs'/ident;c=DATA/'attempt03-control'/ident
        plan=read('/root/ops-competition-2026/plans/'+ident+'.json');terminal=read(c/'controller-terminal.json');receipt=read(c/'ready-receipt.json')
        hs=[read(p) for p in sorted((c/'heartbeats').glob('*.json'))]
        identity=all(h['run_id']==ident and h['run_token']==plan['run_token'] and h['sequence']==i and h['pid']==hs[0]['pid'] and h['producer_sha256']==plan['source_sha256'][str(BASE/'scripts/pilot_observer.py')] and h['checks_sha256']==digest(h['checks']) and h['status']=='LIVE_ENVIRONMENT_PASS' for i,h in enumerate(hs))
        start=stamp(plan['start_utc']);valid={'identity_sequence_integrity':bool(hs) and identity,'ready_before_t0':stamp(receipt['at_utc'])<start,'initial_budget':receipt['startup_seconds']<=15,'post_health':not read(terminal['post_health'])['current_environment_blockers'],'no_business_faults':not terminal['faults'],'two_timers_cancelled':len(terminal.get('cancelled',[]))==2}
        worker={}
        for f in plan['markers']:
            h=f['host'];root=BASE/'runtime'/f['id']
            record={'cancel':optional(h,root/'cancellation.json'),'marker':optional(h,root/'marker.json'),'terminal':optional(h,root/'terminal.json'),'actual':optional(h,root/'actual-start.json')}
            worker[h]=record
            dest=c/('worker-'+h)
            if not dest.exists():copy(h,root,dest,True,True)
            valid[h+'_cancelled']=bool(record['cancel'] and record['cancel']['timer_and_service_inactive'])
        row={'checks':valid,'first_check_seconds':receipt['first_check_seconds'],'startup_seconds':receipt['startup_seconds'],'heartbeat_count':len(hs),'max_heartbeat_gap_seconds':max((stamp(b['completed_utc'])-stamp(a['completed_utc']) for a,b in zip(hs,hs[1:])),default=0),'workers':worker}
        if ident=='A03-NOFAULT-INTEGRATION':
            q=read(run/'capture/chunk-00/quality.json');valid['execution_finished']=terminal['status']=='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY'
            valid['all_completion_audits_pass']=q['status']=='CHUNK_QUALITY_PASS'
            valid['markers_success']=len(terminal.get('markers',{}))==2 and all(x['actual'] and x['actual']['pass'] for x in worker.values())
            valid['timing_continuity']=row['max_heartbeat_gap_seconds']<=30
            samples=[json.loads(l) for l in (run/'safety.jsonl').read_text().splitlines()]
            valid['refused_no_increment']=all(s['collector']['refused']=={'grpc':0.0,'http':0.0} for s in samples)
            valid['window_covered']=stamp(samples[0]['at_utc'])-start<=5 and any(stamp(s['at_utc'])>=start+300 for s in samples)
            req=[]
            for path in ('state-machine/request-events-live.jsonl','controls/control-events-live.jsonl'):req.extend(json.loads(l) for l in (run/path).read_text().splitlines())
            req=[r for r in req if start<=stamp(r['request_started_utc'])<start+300]
            valid['business_success']=all(r['success'] for r in req)
            proxies=set(q['proxy_trace_ids']);guards=[]
            for side in ('start','end'):
                for file in (run/'private-boundary'/side/'trace-storage-pages').glob('*.json'):guards.extend(h['_source'] for h in read(file)['hits']['hits'])
            missing=[r for r in req if r['trace_id'].lower() not in proxies and not any(boundary_match(r,s,start,start+300) for s in guards)]
            valid['all_request_identities_matched']=not missing
            row.update({'requests':len(req),'unexplained_missing':len(missing),'chunk_quality':{k:q[k] for k in ('first_spans','plus60_spans','final_spans','audit_spans','logs','completion_lag_seconds')},'safety_samples':len(samples),'collector_peak_bytes':max(s['collector']['working_set_bytes'] for s in samples)})
        else:
            external=read(c/'external-observer-exit.json')
            valid['expected_exit_detected']=terminal['status']=='CASE_CONTROLLER_INVALID' and ('OBSERVER_ABORT:' in terminal.get('error','') or 'OBSERVER_EXITED' in terminal.get('error','')) and external['signal']=='SIGTERM'
            row['external_exit']=external
            valid['no_marker_dispatched']=all(x['marker'] is None and x['actual'] is None for x in worker.values())
        if not all(valid.values()):fail.append({ident:[k for k,v in valid.items() if not v]})
        findings[ident]=row
    out=BASE/'results/joint-rehearsal-verdict.json'
    value={'status':'JOINT_REHEARSAL_PASS' if not fail else 'JOINT_REHEARSAL_BLOCKED','failures':fail,'results':findings,'actual_business_faults':0,'formal_attempt03_created':False,'at_utc':datetime.now(timezone.utc).isoformat()}
    with out.open('x') as f:json.dump(value,f,indent=2)
    print(json.dumps(value))
    if fail:raise SystemExit(1)
if __name__=='__main__':main()
