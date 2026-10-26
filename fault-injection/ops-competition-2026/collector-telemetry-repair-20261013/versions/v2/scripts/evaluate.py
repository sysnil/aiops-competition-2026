"""Evaluate preregistered no-fault evidence; cannot alter configuration or inputs."""
import argparse,json,gzip,sys,re
from collections import Counter,defaultdict
from datetime import datetime
from common import *
sys.path.insert(0,'/root/stage5-v3-clean-gate-v2/scripts')
from trace_identity import span_map,subset
from evaluate_trace_v2 import classify_late

def ts(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def main():
    p=argparse.ArgumentParser();p.add_argument('--role',choices=['BASE','VAL1','VAL2'],required=True);a=p.parse_args()
    run=DATA/'runs'/a.role;schedule=read(run/'schedule.json');plan=read(ROOT/'plans/validation-v2.json')
    execution=read(run/'execution-result.json');streams={}
    streams['business']=[r for l in (run/'state-machine/sessions.jsonl').read_text().splitlines() for r in json.loads(l)['steps']]
    for name in ('image_asset','cart_view'):streams[name]=[json.loads(l) for l in (run/'controls'/(name+'.jsonl')).read_text().splitlines()]
    classes={k:{r['trace_id'].lower() for r in rows if r.get('trace_id')} for k,rows in streams.items()}
    issued=set().union(*classes.values());allspans={};stages=[];late=[];log_counts=Counter();metric_counts=Counter()
    for name,w in schedule['windows'].items():
        base=run/'capture'/name;dirs=[base/'captures/during',base/'trace-completion/during',base/'trace-final/during',base/'trace-late-audit/during']
        first,mid,final,audit=dirs;fm,cm,fin,am=[read(p) for p in (first/'capture-manifest.json',mid/'completion-manifest.json',final/'final-manifest.json',audit/'late-audit-manifest.json')]
        maps=[span_map(d) for d in dirs]
        for before,after in zip(maps,maps[1:]):subset(before,after)
        for k,v in maps[2].items():
            if k in allspans:raise RuntimeError('Duplicate across exact windows')
            allspans[k]=v
        end=ts(w['end_utc'])
        checks=[fm['completion_lag_seconds']<=60,5<=fm['start_lag_seconds']<=10,
            60<=cm['start_lag_seconds']<=65,(mid/'completion-manifest.json').stat().st_mtime-end<=120,
            120<=ts(fin['actual_start_utc'])-end<=125,ts(fin['completed_utc'])-end<=180,
            240<=ts(am['actual_start_utc'])-end<=245,ts(am['completed_utc'])-end<=300,
            fin['first_manifest_sha256']==sha(first/'capture-manifest.json'),am['final_manifest_sha256']==sha(final/'final-manifest.json')]
        pods={r['metadata']['uid']:r for r in read(first/'kubernetes/pods.json')['items']}
        for k in maps[3].keys()-maps[2].keys():late.append({'chunk':name,'trace_id':k[0],'span_id':k[1],'classification':classify_late(k,maps[3][k][1],maps[3],classes,pods)})
        for file in (first/'metrics').glob('*.json'):
            for s in read(file)['data']['result']:metric_counts[s['metric'].get('__name__','UNKNOWN')]+=len(s['values'])
        for file in (first/'logs').glob('*.json'):
            for h in read(file)['hits']['hits']:log_counts[h['_source'].get('resource',{}).get('service.name','UNKNOWN')]+=1
        stages.append({'chunk':name,'timing_and_manifest_pass':all(checks),'first_spans':len(maps[0]),'plus60_spans':len(maps[1]),
            'final_spans':len(maps[2]),'audit_spans':len(maps[3]),'logs':fm['log_records'],
            'completion_lag_seconds':{'first':fm['completion_lag_seconds'],'plus60':(mid/'completion-manifest.json').stat().st_mtime-end,
                'plus120':ts(fin['completed_utc'])-end,'plus240':ts(am['completed_utc'])-end}})
    proxies=Counter();bytrace=defaultdict(set);uidcounts=Counter();servicecounts=Counter()
    for k,(_,s) in allspans.items():
        service=s['process']['serviceName'];bytrace[k[0]].add(service);servicecounts[service]+=1
        if service=='frontend-proxy' and s.get('tag',{}).get('span@kind')=='server':proxies[k[0]]+=1
        attrs={r['key']:r.get('value') for r in s['process'].get('tags',[])}
        if attrs.get('k8s.pod.uid'):uidcounts[attrs['k8s.pod.uid']]+=1
    matched={};per_request=[]
    for name,rows in streams.items():
        expected=classes[name];missing=expected-proxies.keys()
        matched[name]={'requests':len(rows),'unique_issued':len(expected),'successes':sum(bool(r.get('success')) for r in rows),
            'proxy_matched':len(expected&proxies.keys()),'proxy_missing':len(missing),'missing_fraction':len(missing)/len(expected) if expected else None,
            'proved_sampled_out':0,'missing_ids':sorted(missing),
            'downstream_service_coverage':dict(Counter(s for tid in expected for s in bytrace[tid]))}
        for r in rows:
            tid=(r.get('trace_id') or '').lower()
            per_request.append({'stream':name,'trace_id':tid or None,'proxy_span_count':proxies[tid],
                'request_success':r.get('success'),'client_completion_utc':r.get('time_utc'),
                'stored_services':sorted(bytrace[tid]),'status':'MATCHED' if proxies[tid] else 'UNEXPLAINED_MISSING' if tid else 'NOT_SENT'})
    monitors=[schedule['initial_monitor']]+[json.loads(l) for l in (run/'monitor.jsonl').read_text().splitlines()]
    series=defaultdict(list)
    for n,row in enumerate(monitors):
        for s in row['collector_metrics']['data']['result']:
            series[json.dumps(s['metric'],sort_keys=True)].append((n,float(s['value'][1])))
    statistics=[]
    for labels,points in series.items():
        values=[v for _,v in points];m=json.loads(labels);is_counter=m.get('__name__','').endswith('_total')
        resets=sum(b<a for a,b in zip(values,values[1:])) if is_counter else None
        statistics.append({'labels':m,'samples':len(points),'first':values[0],'last':values[-1],'max':max(values),
            'counter_reset':resets,'delta':values[-1]-values[0] if is_counter and not resets else None})
    def deltas(name,transport=None):
        rows=[r for r in statistics if r['labels'].get('__name__')==name and (transport is None or r['labels'].get('transport')==transport)]
        complete=rows and all(r['samples']==len(monitors) and r['delta'] is not None for r in rows)
        return {'status':'OBSERVED' if complete else 'UNKNOWN_MISSING_OR_RESET_NOT_ZERO','series':rows,'delta':sum(r['delta'] for r in rows) if complete else None}
    refusal={transport:deltas('otelcol_receiver_refused_spans_total',transport) for transport in ('grpc','http')}
    raw=gzip.open(run/'otel-collector-runtime.log.gz','rt').read()
    badlogs=[l for l in raw.splitlines() if any(x in l.lower() for x in ['failed to upload metrics','export timeout','refusing data','dropping data','dropping spans'])]
    memory=[float(s['value'][1]) for row in monitors for s in row['working_set']['data']['result']]
    initial=monitors[0]['envoy_stats'];last=monitors[-1]['envoy_stats'];drop='tracing.opentelemetry.spans_dropped'
    proxy_delta=last.get(drop)-initial.get(drop) if drop in initial and drop in last else None
    checks={'execution_complete':execution['status']=='VALIDATION_EXECUTION_COMPLETE_PENDING_ASSESSMENT',
        'full_rate_controls':all(matched[n]['requests']==schedule['duration_seconds']*rate for n,rate in [('image_asset',1),('cart_view',2)]),
        'all_expected_sampled_requests_proxy_matched':all(r['proxy_missing']==0 and r['requests']==r['unique_issued'] for r in matched.values()),
        'all_business_http_requests_successful':all(r['requests']==r['successes'] for r in matched.values()),
        'receiver_refused_grpc_zero':refusal['grpc']['delta']==0,'receiver_refused_http_zero':refusal['http']['delta']==0,
        'proxy_drop_counter_zero':proxy_delta==0,'no_refusal_or_export_timeout_logs':not badlogs,
        'collector_working_set_safe':max(memory)<380*1024**2,
        'all_chunks_bounded_complete':all(r['timing_and_manifest_pass'] and r['logs']>0 and r['final_spans']>0 for r in stages),
        'no_critical_unclassified_late_spans':all(r['classification']=='VERIFIED_NONCRITICAL_CONTROL_PLANE' for r in late),
        'storage_freshness':all(0<=v['age_seconds']<=60 for r in monitors for v in r['latest_storage'].values()),
        'three_modalities_available':bool(metric_counts and log_counts and servicecounts),
        'requests_within_fixed_observation':all(r.get('time_utc') and ts(r['time_utc'])<ts(schedule['end_utc']) for rows in streams.values() for r in rows),
        'registered_sources_unchanged':all(sha(p)==v for p,v in plan['source_sha256'].items())}
    result={'status':'NO_FAULT_VALIDATION_PASS' if all(checks.values()) else 'NO_FAULT_VALIDATION_FAIL','role':a.role,
        'at_utc':now(),'checks':checks,'blockers':[k for k,v in checks.items() if not v],'matching':matched,'stages':stages,'late_spans':late,
        'receiver_refused':refusal,'receiver_accepted':{t:deltas('otelcol_receiver_accepted_spans_total',t) for t in ('grpc','http')},
        'exporter_failed':deltas('otelcol_exporter_send_failed_spans_total'),'metric_statistics':statistics,
        'limiter_refused':deltas('otelcol_processor_memory_limiter_refused_spans_total'),
        'working_set_peak_bytes':max(memory),'proxy_spans_dropped_delta':proxy_delta,'collector_error_log_matches':badlogs,
        'log_service_counts':dict(log_counts),'trace_service_counts':dict(servicecounts),'metric_sample_counts':dict(metric_counts),
        'pod_uid_span_counts':dict(uidcounts),'storage_freshness_max_seconds':{k:max(r['latest_storage'][k]['age_seconds'] for r in monitors) for k in ['logs','traces']},
        'configuration_sha256':schedule['config_sha256'],'seed':schedule['seed'],'faults_injected':0,'formal_case_created':False}
    save(run/'per-request-match.json',per_request);save(run/'assessment.json',result)
    print(json.dumps({k:result[k] for k in ['status','role','blockers','working_set_peak_bytes','proxy_spans_dropped_delta','storage_freshness_max_seconds']}))
if __name__=='__main__':main()
