"""Immutable identity audit and completed-request accounting; never reads injection evidence."""
import argparse,time,sys
from pilot_common import *
CG=Path('/root/stage5-v3-clean-gate-v2/scripts');sys.path.insert(0,str(CG))
from trace_identity import span_map,subset
from evaluate_trace_v2 import classify_late
def main():
    p=argparse.ArgumentParser();p.add_argument('--chunk',required=True);a=p.parse_args()
    schedule=read(RUN/'schedule.json');w=schedule['windows'][a.chunk];end=stamp(w['end_utc'])
    base=RUN/'capture'/a.chunk;output=base/'quality.json'
    if output.exists():raise RuntimeError('Never overwrite an audit')
    time.sleep(max(0,end+245-time.time()))
    final=base/'trace-final/during';audit=base/'trace-late-audit/during'
    while not (audit/'late-audit-manifest.json').is_file() and time.time()<end+305:time.sleep(1)
    outcome={'chunk':a.chunk,'status':'CHUNK_QUALITY_FAILED','at_utc':now()}
    try:
        first=base/'captures/during';mid=base/'trace-completion/during'
        fm,cm,fin,am=map(read,(first/'capture-manifest.json',mid/'completion-manifest.json',final/'final-manifest.json',audit/'late-audit-manifest.json'))
        if not (5<=fm['start_lag_seconds']<=10 and fm['completion_lag_seconds']<=60 and
            60<=cm['start_lag_seconds']<=65 and (mid/'completion-manifest.json').stat().st_mtime-end<=120 and
            120<=stamp(fin['actual_start_utc'])-end<=125 and stamp(fin['completed_utc'])-end<=180 and
            240<=stamp(am['actual_start_utc'])-end<=245 and stamp(am['completed_utc'])-end<=300):raise RuntimeError('Registered completion deadline failed')
        if fin['first_manifest_sha256']!=sha(first/'capture-manifest.json') or am['final_manifest_sha256']!=sha(final/'final-manifest.json'):raise RuntimeError('Manifest chain broken')
        maps=[span_map(d) for d in (first,mid,final,audit)]
        for x,y in zip(maps,maps[1:]):subset(x,y)
        requests=live_requests();classes={n:{r['trace_id'].lower() for r in requests if r.get('trace_id') and r['stream']==n} for n in ('business','image_asset','cart_view')}
        pods={r['metadata']['uid']:r for r in read(first/'kubernetes/pods.json')['items']}
        late=[{'trace_id':k[0],'span_id':k[1],'classification':classify_late(k,maps[3][k][1],maps[3],classes,pods)} for k in maps[3].keys()-maps[2].keys()]
        if any(r['classification']!='VERIFIED_NONCRITICAL_CONTROL_PLANE' for r in late):raise RuntimeError('Critical/unclassified late span')
        proxies={k[0] for k,(_,s) in maps[2].items() if s['process']['serviceName']=='frontend-proxy' and s.get('tag',{}).get('span@kind')=='server'}
        all_proxies=set(proxies)
        for name in schedule['windows']:
            if name==a.chunk:break
            prior=read(RUN/'capture'/name/'quality.json')
            if prior['status']!='CHUNK_QUALITY_PASS':raise RuntimeError('Earlier chunk failed')
            all_proxies.update(prior['proxy_trace_ids'])
        start=stamp(schedule['start_utc'])
        # One-second boundary guard only defers interim accounting; final assessment has no exclusion.
        due=[r for r in requests if start<=stamp(r['request_started_utc']) and stamp(r['time_utc'])<=end-1]
        if len({r['trace_id'] for r in due})!=len(due):raise RuntimeError('Reused request identity within this run')
        guard_root=RUN/'private-boundary/start'
        guard=span_map(guard_root) if read(guard_root/'trace-storage-source.json')['unique_spans'] else {}
        boundary={r['trace_id'] for r in due if any(boundary_match(r,s,start,stamp(schedule['end_utc'])) for _,s in guard.values())}
        missing=[{'trace_id':r['trace_id'],'stream':r['stream'],'success':r['success'],'error_type':r.get('error_type'),
            'request_started_utc':r['request_started_utc'],'time_utc':r['time_utc']} for r in due if r['trace_id'].lower() not in all_proxies]
        missing=[r for r in missing if r['trace_id'] not in boundary]
        if missing:raise RuntimeError('Unexplained mature request-to-Proxy gap: '+str(len(missing)))
        if fm['log_records']==0 or not maps[2] or not list((first/'metrics').glob('*.json')):raise RuntimeError('Empty modality')
        outcome.update(status='CHUNK_QUALITY_PASS',proxy_trace_ids=sorted(proxies),late_spans=late,
            expected_prefix_requests=len(due),matched_prefix_requests=sum(r['trace_id'].lower() in all_proxies for r in due),
            private_boundary_verified_ids=sorted(boundary),unmatched_request_ids=[],
            first_spans=len(maps[0]),plus60_spans=len(maps[1]),final_spans=len(maps[2]),audit_spans=len(maps[3]),
            logs=fm['log_records'],final_manifest_sha256=sha(final/'final-manifest.json'),
            completion_lag_seconds={'first':fm['completion_lag_seconds'],'plus60':(mid/'completion-manifest.json').stat().st_mtime-end,
                'plus120':stamp(fin['completed_utc'])-end,'plus240':stamp(am['completed_utc'])-end},
            deferred_boundary_policy='next-prefix audit; all requests rechecked without this guard at final evaluation')
    except Exception as e:
        outcome['error']=str(e)
        if 'missing' in locals():outcome['unmatched_requests']=missing
    outcome['completed_utc']=now();save(output,outcome)
    print(json.dumps({k:v for k,v in outcome.items() if k not in ['proxy_trace_ids']}),flush=True)
    if outcome['status']!='CHUNK_QUALITY_PASS':raise SystemExit(1)
if __name__=='__main__':main()
