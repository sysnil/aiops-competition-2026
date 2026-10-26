#!/usr/bin/env python3
"""Validate the registered hour and immutable delayed reads; no retrospective repair."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
from competition_observation import ROOT,DATA,CG,sha,save
from competition_qualification import steps,bucket,stamp

def read(p):return json.loads(p.read_text())
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    sys.path.insert(0,str(CG));from trace_identity import span_map,subset
    from evaluate_trace_v2 import classify_late
    run=DATA/'runs/SF-PILOT-01';schedule=read(run/'schedule.json')
    plan=read(ROOT/'plans/SF-PILOT-01.json');execution=read(ROOT/'runs/SF-PILOT-01/controller-terminal.json')
    req=steps(run);controls={n:[json.loads(l) for l in (run/'controls'/(n+'.jsonl')).read_text().splitlines()]
        for n in ('image_asset','cart_view')}
    classes={'business':{r['trace_id'].lower() for r in req if r.get('trace_id')},
        **{n:{r['trace_id'].lower() for r in rows if r.get('trace_id')} for n,rows in controls.items()}}
    start=stamp(schedule['start_utc']);end=stamp(schedule['end_utc']);baseline=bucket(req,start,start+720)
    recoveries={'service':bucket(req,start+1800,start+2040),'final':bucket(req,end-180,end)}
    checks={'complete_hour':end-start==3600,'controller_complete':execution['status']=='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY',
        'observer_complete':read(run/'observation-result.json')['status']=='CASE_OBSERVATION_COMPLETE_PENDING_DATA_QUALITY',
        'both_injections_verified':len(execution['faults'])==2 and all(r['status']=='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'
            and abs(r.get('duration_seconds',0)-300)<=2 and r.get('cleanup',{}).get('status')=='COMPETITION_CLEANUP_ACTIONS_VERIFIED' for r in execution['faults'].values()),
        'all_sources_unchanged':all(sha(p)==h for p,h in plan['source_sha256'].items()),
        'baseline_business_clean':bool(baseline) and all(r.get('success') for r in baseline),
        'independent_image_control':bool(controls['image_asset']) and all(r.get('success') for r in controls['image_asset']),
        'both_recovery_business_windows':all(rows and all(r.get('success') for r in rows) for rows in recoveries.values())}
    health=read(Path(execution['post_health']))
    checks['post_health_and_five_host_residue']=health['status']=='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' and not health['current_environment_blockers']
    details=[];next_start=start
    for name,w in schedule['windows'].items():
        base=run/'capture'/name;first=base/'captures/during';middle=base/'trace-completion/during'
        final=base/'trace-final/during';audit=base/'trace-late-audit/during'
        fm,mm,fin,am=read(first/'capture-manifest.json'),read(middle/'identity-audit.json'),read(final/'final-manifest.json'),read(audit/'late-audit-manifest.json')
        boundary=stamp(w['end_utc'])
        valid=stamp(w['start_utc'])==next_start and boundary-next_start==600
        next_start=boundary
        valid=valid and all(fm['window'][k]==w[k] for k in ('start_utc','end_utc')) and fm['trace_source']=='JAEGER_OPENSEARCH_STORAGE'
        valid=valid and fm['log_records']>0 and fm['span_records']>0 and fin['status']=='FINAL_SNAPSHOT_FROZEN' and mm['status']=='IDENTITY_PASS'
        valid=valid and 120<=stamp(fin['actual_start_utc'])-boundary<=125 and stamp(fin['completed_utc'])-boundary<=180
        valid=valid and 240<=stamp(am['actual_start_utc'])-boundary<=245 and stamp(am['completed_utc'])-boundary<=300
        valid=valid and fin['first_manifest_sha256']==sha(first/'capture-manifest.json') and am['final_manifest_sha256']==sha(final/'final-manifest.json')
        valid=valid and all(sha(first/k)==digest for k,digest in fm['sha256'].items())
        one,two,three,four=map(span_map,(first,middle,final,audit));subset(one,two);subset(two,three);subset(three,four)
        pods={r['metadata']['uid']:r for r in read(first/'kubernetes/pods.json')['items']}
        late=[{'trace_id':k[0],'span_id':k[1],'class':classify_late(k,four[k][1],four,classes,pods)} for k in four.keys()-three.keys()]
        valid=valid and all(r['class']=='VERIFIED_NONCRITICAL_CONTROL_PLANE' for r in late)
        actualids={k[0] for k in three};positive={n:len(ids&actualids) for n,ids in classes.items()}
        valid=valid and positive['business']>=100 and positive['image_asset']>=30 and positive['cart_view']>=30
        details.append({'chunk':name,'pass':valid,'first_spans':len(one),'plus60_spans':len(two),'final_spans':len(three),
            'late_spans':late,'logs':fm['log_records'],'positive_control_matches':positive,
            'first_manifest_sha256':sha(first/'capture-manifest.json'),'final_manifest_sha256':sha(final/'final-manifest.json')})
    checks['six_contiguous_bounded_captures']=len(details)==6 and next_start==end and all(r['pass'] for r in details)
    safety=[json.loads(l) for l in (run/'safety.jsonl').read_text().splitlines()]
    checks['continuous_safety']=all(r['status']=='LIVE_ENVIRONMENT_PASS' for r in safety) and len(safety)>=240
    result={'status':'CONTEST_CAPTURE_QUALITY_PASS' if all(checks.values()) else 'CONTEST_CAPTURE_QUALITY_FAIL',
        'checks':checks,'blockers':[k for k,v in checks.items() if not v],'chunks':details,
        'issued_requests':len(req),'failed_requests':sum(not r.get('success') for r in req),
        'image_requests':len(controls['image_asset']),'cart_control_requests':len(controls['cart_view']),
        'at_utc':datetime.now(timezone.utc).isoformat(),'data_modified':False,'anomaly_gt_finalized':False}
    save(a.output,result);print(json.dumps(result))
    if not all(checks.values()):raise SystemExit(1)

if __name__=='__main__':main()
