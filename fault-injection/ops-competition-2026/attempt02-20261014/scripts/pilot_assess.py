"""Final immutable public-evidence/injection/recovery assessment before any release."""
from collections import Counter,defaultdict
import statistics,sys
from pilot_common import *
from competition_qualification import span_error
sys.path.insert(0,'/root/stage5-v3-clean-gate-v2/scripts')
from trace_identity import span_map
def percentile(v):return sorted(v)[int((len(v)-1)*.95)] if v else None
def main():
    plan=read(OPS/'plans/SF-PILOT-01-ATTEMPT-02.json');schedule=read(RUN/'schedule.json');terminal=read(PROD/'results/controller-terminal.json')
    lo,hi=stamp(schedule['start_utc']),stamp(schedule['end_utc']);requests=live_requests()
    eligible=[r for r in requests if lo<=stamp(r['request_started_utc'])<hi];outside=[r for r in requests if not lo<=stamp(r['request_started_utc'])<hi]
    if len({r['trace_id'] for r in eligible})!=len(eligible):raise RuntimeError('Request identity reuse')
    proxies=set();spans={};chunks=[];memory=[]
    for name,w in schedule['windows'].items():
        base=RUN/'capture'/name;q=read(base/'quality.json');chunks.append(q)
        proxies.update(q.get('proxy_trace_ids',[]))
        # Only source rows needed for effect verification are retained in memory.
        for file in sorted((base/'trace-late-audit/during/trace-storage-pages').glob('page-*.json')):
            for hit in read(file)['hits']['hits']:
                s=hit['_source']
                if s['process']['serviceName'] in ('frontend','recommendation','frontend-proxy'):
                    spans[(s['traceID'].lower(),s['spanID'].lower())]=s
        for series in read(base/'captures/during/metrics/memory.json')['data']['result']:
            if series['metric'].get('container') not in (None,'','POD'):
                memory.append({**series,'values':[(t,v) for t,v in series['values'] if stamp(w['start_utc'])<=float(t)<stamp(w['end_utc'])]})
    guard={}
    for side in ('start','end'):
        p=RUN/'private-boundary'/side
        if read(p/'trace-storage-source.json')['unique_spans']:guard.update(span_map(p))
    boundary=[];missing=[];matching=defaultdict(Counter)
    for r in eligible:
        m=matching[r['stream']];m['expected']+=1;m['http_success']+=int(r['success'])
        if r['trace_id'].lower() in proxies:m['public_window_proxy_matched']+=1;continue
        proof=[s for _,s in guard.values() if boundary_match(r,s,lo,hi)]
        if proof:
            m['private_boundary_verified']+=1;boundary.append({'request':r,'span_ids':[s['spanID'] for s in proof],
                'span_start_us':[s['startTime'] for s in proof],'classification':'REAL_SPAN_OUTSIDE_PUBLIC_EVENT_WINDOW'})
        else:m['unexplained_missing']+=1;missing.append(r)
    monitors=[json.loads(l) for l in (RUN/'safety.jsonl').read_text().splitlines()]
    chunks_pass=all(q['status']=='CHUNK_QUALITY_PASS' for q in chunks)
    initial=schedule['initial_telemetry']['refused'];refused_ok=all(r['collector']['refused']==initial for r in monitors)
    controls={n:read(RUN/'controls/result-manifest.json')['streams'][n] for n in ('image_asset','cart_view')}
    controls_ok=all(v['missed_arrivals']==0 and v['successful_requests']==v['planned_requests']==3600*rate for (n,rate),v in [(('image_asset',1),controls['image_asset']),(('cart_view',2),controls['cart_view'])])
    injection_ok=len(terminal.get('faults',{}))==2 and all(
        r['status']=='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED' and abs(r['duration_seconds']-300)<=2 and
        r.get('cleanup',{}).get('status')=='COMPETITION_CLEANUP_ACTIONS_VERIFIED' for r in terminal.get('faults',{}).values())
    recoveries={layer:read(RUN/('recovery-'+layer.lower()+'.json')) for layer in ('SERVICE','POD')}
    # Public Service effects, independent from private SIGSTOP evidence.
    relevant=[r for r in eligible if lo+720<=stamp(r['request_started_utc'])<lo+1020 and 'recommendations' in r.get('path','')]
    error_ids={s['traceID'].lower() for s in spans.values() if span_error(s)}
    errors=[r for r in relevant if not r['success'] and r['trace_id'].lower() in error_ids]
    parents=set()
    for s in spans.values():
        if s['process']['serviceName']=='recommendation' and s.get('tag',{}).get('span@kind')=='server':
            parents.update((r['traceID'].lower(),r['spanID'].lower()) for r in s.get('references') or [] if r.get('refType')=='CHILD_OF')
    durations={'before':[],'during':[]}
    for key in parents:
        s=spans.get(key)
        if not s or s['process']['serviceName']!='frontend':continue
        t=s['startTime']/1e6
        if lo<=t<lo+720:durations['before'].append(s['duration']/1000)
        if lo+720<=t<lo+1020:durations['during'].append(s['duration']/1000)
    bp,dp=percentile(durations['before']),percentile(durations['during'])
    service_effect={'matched_error_requests':len(errors),'evidence_trace_ids':[r['trace_id'] for r in errors],
        'caller_p95_ms':{'before':bp,'during':dp},'caller_span_counts':{k:len(v) for k,v in durations.items()}}
    service_effect['pass']=len(errors)>=3 or (len(durations['before'])>=10 and len(durations['during'])>=10 and dp>=2*bp)
    # Public UID-linked Pod allocation signal and sibling contrast.
    target=read(OPS/'plans/SF-PILOT-01-ATTEMPT-02-POD.json')['targets'][0]
    sibling=next(t for t in plan['targets_and_siblings'] if t['service']=='frontend' and t['uid']!=target['uid'])
    def values(entity,start,end):
        result={}
        for series in memory:
            labels=series['metric']
            if labels.get('pod')==entity['name'] and entity['uid'].replace('-','_') in labels.get('id',''):
                for t,v in series['values']:
                    if start<=float(t)<end:
                        if float(t) in result and result[float(t)]!=float(v):raise RuntimeError('Conflicting memory sample')
                        result[float(t)]=float(v)
        return [v for _,v in sorted(result.items())]
    b=values(target,lo+1800,lo+2100);d=values(target,lo+2100,lo+2400);after=values(target,lo+2520,lo+2700)
    sb=values(sibling,lo+1800,lo+2100);sd=values(sibling,lo+2100,lo+2400)
    median=statistics.median(b);mad=statistics.median(abs(v-median) for v in b);threshold=max(16*1024**2,5*mad)
    best=count=0
    for v in d:count=count+1 if v>=median+threshold else 0;best=max(best,count)
    delta=statistics.median(d)-median;peer=statistics.median(sd)-statistics.median(sb)
    pod_effect={'pod_uid':target['uid'],'baseline_median_bytes':median,'baseline_mad_bytes':mad,'delta_bytes':delta,
        'sibling_delta_bytes':peer,'consecutive_abnormal_samples':best,'recovered':bool(after) and all(v<=median+threshold for v in after),
        'pass':best>=2 and delta-peer>=8*1024**2}
    health=read(terminal['post_health'])
    checks={'continuous_hour':hi-lo==3600,'execution_completed':terminal['status']=='CASE_EXECUTION_COMPLETE_PENDING_DATA_QUALITY',
        'both_faults_valid':injection_ok,'both_recoveries_publicly_verified':all(r['status']=='PUBLIC_RECOVERY_PASS' for r in recoveries.values()) and pod_effect['recovered'],
        'all_registered_chunks_pass':chunks_pass,'all_expected_requests_explained':not missing,'full_rate_controls_successful':controls_ok,
        'collector_refused_no_increment':refused_ok,'service_public_anomaly':service_effect['pass'],'pod_public_anomaly':pod_effect['pass'],
        'final_actual_safety':health['status']=='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' and not health['current_environment_blockers']}
    result={'status':'CASE_CAPTURE_AND_EFFECTS_PASS' if all(checks.values()) else 'CASE_CAPTURE_AND_EFFECTS_FAIL',
        'at_utc':now(),'checks':checks,'blockers':[k for k,v in checks.items() if not v],
        'expected_request_counts':{k:dict(v) for k,v in matching.items()},'private_boundary_records':boundary,
        'unmatched_requests':missing,'outside_window_requests':outside,'service_effect':service_effect,'pod_effect':pod_effect,
        'collector_working_set_peak_bytes':max(r['collector']['working_set_bytes'] for r in monitors),
        'schema_and_leak_validation':'PENDING_EXPORT','gt_status':'DRAFT_PENDING_HUMAN_REVIEW'}
    save(RUN/'capture-and-effects-assessment.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['private_boundary_records','unmatched_requests','outside_window_requests','service_effect']}))
    if not all(checks.values()):raise SystemExit(1)
if __name__=='__main__':main()
