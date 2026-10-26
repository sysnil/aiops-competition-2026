#!/usr/bin/env python3
"""Read-only final accounting; preserve original evaluator decisions and later caveats."""
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
from competition_archive import sha

ROOT=Path('/root/ops-competition-2026');DATA=Path('/data/stage5-v3/ops-competition-2026')
IDS=['COMP-SF01-SERVICE-SHORT','COMP-SF01-SERVICE-300','COMP-SF01-POD-SHORT','COMP-SF01-POD-300']
def read(p):return json.loads(p.read_text())
def main():
    rows=[]
    for ident in IDS:
        run=DATA/'qualification'/ident;decision_path=ROOT/'qualification'/ident/'decision.json'
        decision=read(decision_path);schedule=read(run/'schedule.json');injection=read(run/'private-injection/injection-result.json')
        metrics=0;logs=0;spans=set();traceids=set();proxy=set();minutes={};metric_names=set()
        for phase in ('before','during','after'):
            first=run/'capture/captures'/phase
            logs+=read(first/'capture-manifest.json')['log_records']
            for file in (first/'metrics').glob('*.json'):
                for s in read(file)['data']['result']:
                    metrics+=len(s['values']);metric_names.add(s['metric'].get('__name__'))
            for file in sorted((run/'capture/trace-final'/phase/'trace-storage-pages').glob('page-*.json')):
                for hit in read(file)['hits']['hits']:
                    s=hit['_source'];key=(s['traceID'].lower(),s['spanID'].lower())
                    if key in spans:raise RuntimeError('Cross-window duplicate span')
                    spans.add(key);traceids.add(key[0])
                    if s['process']['serviceName']=='frontend-proxy' and (s.get('tag') or {}).get('span@kind')=='server':proxy.add(key[0])
        controls={}
        for stream in ('image_asset','cart_view'):
            issued=[json.loads(l) for l in (run/'controls'/(stream+'.jsonl')).read_text().splitlines()]
            ids={r['trace_id'].lower() for r in issued};missing=ids-proxy
            controls[stream]={'requests':len(issued),'successes':sum(bool(r.get('success')) for r in issued),
                'unique_issued_trace_ids':len(ids),'matched_proxy_trace_ids':len(ids&proxy),'missing_proxy_trace_ids':len(missing),
                'missing_examples':sorted(missing)[:20]}
        safety=[json.loads(l) for l in (run/'safety.jsonl').read_text().splitlines()]
        values=lambda row:{json.dumps(r['metric'],sort_keys=True):float(r['value'][1]) for r in row['collector_refused_raw_series']}
        first,last=values(safety[0]),values(safety[-1])
        deltas=[{'labels':json.loads(k),'first':first[k],'last':last[k],'delta':last[k]-first[k]} for k in first.keys()&last.keys()]
        rows.append({'id':ident,'decision':decision,'decision_sha256':sha(decision_path),'schedule':schedule['windows'],
            'injection':injection,'raw_metric_sample_rows_including_query_endpoints':metrics,'metric_names':sorted(metric_names),
            'logs':logs,'final_unique_spans':len(spans),'final_unique_traces':len(traceids),'controls':controls,
            'collector_refused_counter_deltas':deltas,'counter_series_changed':set(first)!=set(last),
            'raw_bytes_including_immutable_read_versions':sum(p.stat().st_size for p in run.rglob('*') if p.is_file()),
            'source_directory':str(run)})
    lock=read(ROOT/'plans/competition-source-lock-v1.json')
    unchanged=all(sha(Path(p))==digest for p,digest in lock['source_sha256'].items())
    if not unchanged:raise RuntimeError('Qualification code/contract changed')
    value={'status':'SF_PILOT_01_PARTIAL','at_utc':datetime.now(timezone.utc).isoformat(),
        'qualification_results':rows,'qualification_contract_unchanged':unchanged,
        'source_lock_sha256':sha(ROOT/'plans/competition-source-lock-v1.json'),
        'contract_sha256':sha(ROOT/'plans/competition-fault-admission.yaml'),
        'actual_qualification_faults':len(rows),'formal_hour_started':(DATA/'runs/SF-PILOT-01').exists(),
        'production_readiness':read(ROOT/'qualification/production-telemetry-readiness.json'),
        'root_free_bytes':shutil.disk_usage('/').free,'data_free_bytes':shutil.disk_usage('/data/stage5-v3').free,
        'contest_server_accessed':False,'test_gt_accessed':False,'research_assets_changed':False}
    path=ROOT/'qualification/qualification-summary-final.json'
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(json.dumps({'status':value['status'],'mechanism_statuses':[r['decision']['status'] for r in rows],
        'root_free_bytes':value['root_free_bytes'],'data_free_bytes':value['data_free_bytes'],'formal_hour_started':value['formal_hour_started']}))

if __name__=='__main__':main()
