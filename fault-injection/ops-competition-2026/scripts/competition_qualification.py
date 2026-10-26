#!/usr/bin/env python3
"""Competition-only injection, public anomaly, bounded capture and recovery assessment."""

import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import statistics
import sys

ROOT=Path('/root/ops-competition-2026')
DATA=Path('/data/stage5-v3/ops-competition-2026/qualification')
CG=Path('/root/stage5-v3-clean-gate-v2/scripts')

def read(p):return json.loads(Path(p).read_text())
def stamp(x):return datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
    p.chmod(0o600)

def load_sources():
    sys.path.insert(0,str(CG))
    from trace_identity import span_map,subset
    from evaluate_trace_v2 import classify_late
    return span_map,subset,classify_late

def steps(run):
    rows=[]
    for line in (run/'state-machine/sessions.jsonl').read_text().splitlines():
        rows.extend(json.loads(line)['steps'])
    return rows

def bucket(rows,start,end):
    return [r for r in rows if start<=stamp(r['time_utc'])-float(r.get('latency_ms') or 0)/1000<end]

def span_error(row):
    tags={r.get('key'):r.get('value') for r in row.get('tags',[])}
    tags.update({k.replace('@','.'):v for k,v in (row.get('tag') or {}).items()})
    if tags.get('error') in (True,'true',1) or str(tags.get('otel.status_code','')).upper()=='ERROR':return True
    for key in ('http.status_code','http.response.status_code'):
        try:
            if int(tags.get(key,0))>=400:return True
        except (ValueError,TypeError):pass
    try:return int(tags.get('rpc.grpc.status_code',0))!=0
    except (ValueError,TypeError):return False

def memory(run,phase,target):
    rows=read(run/'capture/captures'/phase/'metrics/memory.json')['data']['result']
    selected=[r for r in rows if r['metric'].get('pod')==target['name'] and
        target['uid'].replace('-','_') in r['metric'].get('id','') and
        r['metric'].get('container') not in (None,'','POD')]
    if len(selected)!=1:raise RuntimeError('Public target UID memory series not unique')
    return [(float(t),float(v)) for t,v in selected[0]['values']]

def evaluate(run,plan,inject,posthealth):
    observation=read(run/'observation-result.json');schedule=read(run/'schedule.json')
    span_map,subset,classify_late=load_sources()
    req=steps(run);issued={r['trace_id'].lower() for r in req if r.get('trace_id')}
    controls={n:[json.loads(l) for l in (run/'controls'/(n+'.jsonl')).read_text().splitlines()]
              for n in ('image_asset','cart_view')}
    classes={'business':issued,**{n:{r['trace_id'].lower() for r in rows if r.get('trace_id')} for n,rows in controls.items()}}
    allspans={};late=[];capturechecks={};perphase={}
    for phase,w in schedule['windows'].items():
        base=run/'capture';first=base/'captures'/phase;middle=base/'trace-completion'/phase
        final=base/'trace-final'/phase;audit=base/'trace-late-audit'/phase
        fm,fin,am=read(first/'capture-manifest.json'),read(final/'final-manifest.json'),read(audit/'late-audit-manifest.json')
        if any(fm['window'][k]!=w[k] for k in ('start_utc','end_utc')) or fm['trace_source']!='JAEGER_OPENSEARCH_STORAGE':
            raise RuntimeError('Capture source or UTC window mismatch')
        a,b,c,d=span_map(first),span_map(middle),span_map(final),span_map(audit)
        subset(a,b);subset(b,c);subset(c,d)
        pods={p['metadata']['uid']:p for p in read(first/'kubernetes/pods.json')['items']}
        end=stamp(w['end_utc'])
        capturechecks[phase]=(fm['log_records']>0 and fm['span_records']>0 and
            fin['status']=='FINAL_SNAPSHOT_FROZEN' and 120<=stamp(fin['actual_start_utc'])-end<=125 and
            stamp(fin['completed_utc'])-end<=180 and 240<=stamp(am['actual_start_utc'])-end<=245 and
            stamp(am['completed_utc'])-end<=300)
        for key in d.keys()-c.keys():
            late.append({'phase':phase,'trace_id':key[0],'span_id':key[1],
                'classification':classify_late(key,d[key][1],d,classes,pods)})
        for key,content in c.items():
            if key in allspans and allspans[key]!=content:raise RuntimeError('Conflicting cross-phase span')
            allspans[key]=content
        perphase[phase]={'logs':fm['log_records'],'final_spans':len(c),'late_spans':len(d)-len(c)}
    spans={k:v[1] for k,v in allspans.items()}
    windows={p:(stamp(w['start_utc']),stamp(w['end_utc'])) for p,w in schedule['windows'].items()}
    groups={p:bucket(req,*w) for p,w in windows.items()}
    baseline_ok=bool(groups['before']) and all(r.get('success') for r in groups['before'])
    recovery_requests=bucket(req,windows['after'][1]-60,windows['after'][1])
    recovery_ok=bool(recovery_requests) and all(r.get('success') for r in recovery_requests)
    image_ok=all(r.get('success') for r in controls['image_asset'])
    coverage={}
    for target in plan['targets_and_siblings']:
        counts={p:0 for p in windows}
        for s in spans.values():
            if s['process']['serviceName']!=target['service'] or (s.get('tag') or {}).get('span@kind')!='server':continue
            uid={r.get('key'):r.get('value') for r in s['process'].get('tags') or []}.get('k8s.pod.uid')
            if uid!=target['uid']:continue
            for phase,(lo,hi) in windows.items():
                if lo<=s['startTime']/1e6<hi:counts[phase]+=1
        coverage[target['uid']]=counts
    involved=[t for t in plan['targets_and_siblings'] if t['service']==plan['targets'][0]['service']]
    baseline_coverage=all(coverage[t['uid']]['before']>=10 for t in involved)
    recovery_coverage=all(coverage[t['uid']]['after']>=10 for t in involved)
    public_evidence={};anomaly=False
    if plan['layer']=='SERVICE':
        related=[r for r in groups['during'] if 'recommendations' in r.get('path','')]
        error_traces={s['traceID'].lower() for s in spans.values() if span_error(s)}
        failures=[r for r in related if not r.get('success') and r['trace_id'].lower() in error_traces]
        parents=set()
        for s in spans.values():
            if s['process']['serviceName']=='recommendation' and (s.get('tag') or {}).get('span@kind')=='server':
                parents.update((r.get('traceID','').lower(),r.get('spanID','').lower()) for r in s.get('references') or [])
        latency={phase:[] for phase in ('before','during')}
        for key in parents:
            s=spans.get(key)
            if not s or s['process']['serviceName']!='frontend' or (s.get('tag') or {}).get('span@kind')!='client':continue
            for phase in latency:
                lo,hi=windows[phase]
                if lo<=s['startTime']/1e6<hi:latency[phase].append(s['duration']/1000)
        def p95(values):
            values=sorted(values);return values[int((len(values)-1)*.95)] if values else None
        before_p95,during_p95=p95(latency['before']),p95(latency['during'])
        latency_anomaly=(len(latency['before'])>=10 and len(latency['during'])>=10 and
            before_p95 is not None and during_p95 is not None and during_p95>=2*before_p95)
        rec_uids=[t['uid'] for t in plan['targets']]
        before_count=sum(coverage[uid]['before'] for uid in rec_uids)
        during_count=sum(coverage[uid]['during'] for uid in rec_uids)
        completion_ratio=(during_count/(windows['during'][1]-windows['during'][0]))/(before_count/(windows['before'][1]-windows['before'][0])) if before_count else None
        completion_anomaly=len(related)>=30 and completion_ratio is not None and completion_ratio<.2 and image_ok
        public_evidence={'kind':'PUBLIC_RECOMMENDATION_CALLER_EVIDENCE',
            'related_requests':len(related),'failed_with_public_error_spans':len(failures),
            'trace_ids':[r['trace_id'] for r in failures],
            'client_parent_span_count':{p:len(v) for p,v in latency.items()},
            'caller_p95_ms':{'before':before_p95,'during':during_p95},
            'caller_latency_anomaly':latency_anomaly,'public_completion_rate_ratio':completion_ratio,
            'completion_anomaly_with_issued_coverage':completion_anomaly}
        anomaly=len(failures)>=3 or latency_anomaly or completion_anomaly
    else:
        target=plan['targets'][0];sibling=next(t for t in involved if t['uid']!=target['uid'])
        b=[v for _,v in memory(run,'before',target)];d=[v for _,v in memory(run,'during',target)]
        after=[v for _,v in memory(run,'after',target)][-4:]
        sb=[v for _,v in memory(run,'before',sibling)];sd=[v for _,v in memory(run,'during',sibling)]
        base=statistics.median(b);mad=statistics.median(abs(v-base) for v in b)
        threshold=max(16*1024**2,5*mad)
        flags=[v>=base+threshold for v in d];consecutive=best=0
        for flag in flags:consecutive=consecutive+1 if flag else 0;best=max(best,consecutive)
        delta=statistics.median(d)-base;peer=statistics.median(sd)-statistics.median(sb)
        recovered=bool(after) and all(v<=base+threshold for v in after)
        public_evidence={'kind':'PUBLIC_UID_LINKED_CONTAINER_WORKING_SET','pod_uid':target['uid'],
            'baseline_median_bytes':base,'baseline_mad_bytes':mad,'during_median_increase_bytes':delta,
            'sibling_centered_increase_bytes':peer,'minimum_threshold_bytes':threshold,
            'consecutive_abnormal_samples':best,'memory_recovered':recovered,
            'source_files':[str(run/'capture/captures'/p/'metrics/memory.json') for p in ('before','during','after')]}
        anomaly=best>=2 and delta-peer>=8*1024**2
        recovery_ok=recovery_ok and recovered
    injection_ok=(inject['status']=='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' and
        abs(inject['duration_seconds']-plan['duration_seconds'])<=2 and
        inject.get('cleanup',{}).get('status')=='COMPETITION_CLEANUP_ACTIONS_VERIFIED')
    checks={'injection_valid':injection_ok,'observation_complete':observation['status']=='OBSERVATION_COMPLETE_NOT_FAULT_QUALIFICATION',
        'three_phase_captures':all(capturechecks.values()),
        'no_critical_or_unclassified_late_spans':all(r['classification']=='VERIFIED_NONCRITICAL_CONTROL_PLANE' for r in late),
        'baseline_no_client_errors':baseline_ok,'target_and_sibling_baseline_coverage':baseline_coverage,
        'public_observed_anomaly':anomaly,'independent_image_business_control':image_ok,
        'recovery_requests_and_resources':recovery_ok,'recovery_trace_coverage':recovery_coverage,
        'postfault_actual_environment':posthealth['status']=='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' and not posthealth['current_environment_blockers']}
    good=all(checks.values())
    label=('COMPETITION_'+plan['layer']+('_QUALIFIED' if plan['duration_seconds']==300 else '_SHORT_VALIDATED')) if good else 'COMPETITION_'+plan['layer']+'_CONDITIONAL'
    return {'id':plan['id'],'status':label,'qualified':good,'at_utc':datetime.now(timezone.utc).isoformat(),
        'actual_injection_duration_seconds':inject.get('duration_seconds'),'checks':checks,
        'blockers':[k for k,v in checks.items() if not v],'public_anomaly_evidence':public_evidence,
        'per_pod_business_server_span_coverage':coverage,'capture_counts':perphase,'late_audit':late,
        'private_injection_used_as_public_anomaly_evidence':False,
        'research_qualification_modified':False,'formal_case_created':False}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--injection-result',type=Path,required=True);parser.add_argument('--post-health',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    p=read(args.plan)
    try:result=evaluate(DATA/p['id'],p,read(args.injection_result),read(args.post_health))
    except Exception as error:result={'id':p['id'],'status':'QUALIFICATION_EVIDENCE_INCOMPLETE','qualified':False,'error':type(error).__name__+': '+str(error)}
    save(args.output,result);print(json.dumps(result),flush=True)
    if not result['qualified']:raise SystemExit(1)

if __name__=='__main__':main()
