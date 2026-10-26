"""Correct historical forensic queries with source-index and per-run time provenance."""
from collections import Counter,defaultdict
from datetime import datetime
from common import *
def main():
    original=Path('/data/stage5-v3/ops-competition-2026/qualification/COMP-SF01-SERVICE-300')
    schedule=read(original/'schedule.json');lo=datetime.fromisoformat(schedule['start_utc']);hi=datetime.fromisoformat(schedule['end_utc'])
    old=read('/data/stage5-v3/ops-competition-2026/audits/collector-late-diagnostic-20261013.json');ids=sorted(old['issued_records'])
    out=DATA/'historical-time-scoped-forensics'
    if out.exists():raise RuntimeError('Immutable diagnosis')
    out.mkdir(mode=0o700)
    span_index='jaeger-jaeger-span-'+lo.date().isoformat();log_index='otel-logs-'+lo.date().isoformat()
    results={}
    for label,index,identity,field,start,end in [('spans',span_index,'traceID','startTime',int(lo.timestamp()*1e6),int(hi.timestamp()*1e6)),
        ('logs',log_index,'traceId.keyword','@timestamp',lo.isoformat(),hi.isoformat())]:
        query={'size':10000,'track_total_hits':True,'query':{'bool':{'filter':[{'terms':{identity:ids}},{'range':{field:{'gte':start,'lt':end}}}]}}}
        response=http(OS+'/'+index+'/_search',query)
        if response['_shards']['total']<1 or response['_shards']['failed'] or response['hits']['total']['relation']!='eq' or response['hits']['total']['value']!=len(response['hits']['hits']):raise RuntimeError('Unbounded or zero-source query')
        save(out/(label+'-query.json'),{'index':index,'query':query,'index_settings':http(OS+'/'+index+'/_settings')})
        save(out/(label+'-response.json'),response);results[label]=response['hits']['hits']
    spans=defaultdict(list);logs=defaultdict(list)
    for h in results['spans']:spans[h['_source']['traceID'].lower()].append(h['_source'])
    for h in results['logs']:logs[h['_source']['traceId'].lower()].append(h['_source'])
    rows=[]
    for tid in ids:
        ss=spans[tid];ll=logs[tid]
        access=[r for r in ll if r.get('resource',{}).get('service.name')=='frontend-proxy' and r.get('attributes',{}).get('event.name')=='proxy.access']
        proxy=[r for r in ss if r['process']['serviceName']=='frontend-proxy' and r.get('tag',{}).get('span@kind')=='server']
        rows.append({'trace_id':tid,'issued':old['issued_records'][tid],'proxy_span_count':len(proxy),
            'proxy_access_log_count':len(access),'proxy_access_log_span_ids':sorted({r.get('spanId') for r in access if r.get('spanId')}),
            'stored_services':dict(Counter(r['process']['serviceName'] for r in ss)),
            'classification':'ACCESS_PROVES_PROXY_ARRIVAL_CONTEXT_BUT_FINAL_PROXY_SPAN_ABSENT' if access and not proxy else 'PROXY_FOUND_IN_SOURCE_WINDOW' if proxy else 'UNRESOLVED_END_TO_END_GAP'})
    result={'status':'TIME_SCOPED_SOURCE_VERIFIED_FORENSICS','at_utc':now(),'window':{'start':lo.isoformat(),'end':hi.isoformat()},
        'requested_ids':len(ids),'proxy_found':sum(r['proxy_span_count']>0 for r in rows),
        'proxy_access_log_matched':sum(r['proxy_access_log_count']>0 for r in rows),'rows':rows,
        'corrections':{'old_zero_shard_late_query':'invalid absence evidence; retained unchanged',
            'unscoped_47_proxy_hits':'later qualification reused control seed namespace; not delayed original spans',
            'this_query':'explicit real index, positive shard count, exact original time interval'},
        'exact_per_span_rejection_causality':'NOT_PROVEN; counters are not per-ID acknowledgements'}
    save(out/'summary.json',result)
    print(json.dumps({k:result[k] for k in ['status','requested_ids','proxy_found','proxy_access_log_matched']}))
if __name__=='__main__':main()
