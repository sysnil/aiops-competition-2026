"""Exploratory private anomaly draft from public observations only. Not an admission gate."""
import argparse,json,sys,statistics
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'code/scripts'))
from pilot_annotations import rows,error,p95,utc
from pilot_exchange_schema import iso_ns
from pilot_export import sha

def main():
 p=argparse.ArgumentParser();p.add_argument('--public',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise RuntimeError('IMMUTABLE_ANALYSIS_OUTPUT_EXISTS')
 m=json.loads((a.public/'manifest.json').read_text());start=iso_ns(m['window']['start_utc'])
 for n,v in m['files'].items():
  if sha(a.public/n)!=v['sha256']:raise RuntimeError('PUBLIC_HASH')
 client={};edges=defaultdict(lambda:defaultdict(list));joined=set()
 cols=['trace_id','span_id','parent_span_id','parent_service','service','start_time','duration_ms','attributes']
 for r in rows(a.public/'traces.parquet',cols):
  attrs=json.loads(r['attributes'] or '{}')
  if attrs.get('span.kind')=='client':client[(r['trace_id'].lower(),r['span_id'].lower())]={**r,'is_error':error(attrs)}
 for r in rows(a.public/'traces.parquet',cols):
  attrs=json.loads(r['attributes'] or '{}')
  if attrs.get('span.kind')!='server' or not r['parent_span_id']:continue
  k=(r['trace_id'].lower(),r['parent_span_id'].lower());parent=client.get(k)
  if not parent or parent['service']==r['service'] or k in joined:continue
  joined.add(k);i=int((parent['start_time']-start)//(60*10**9))
  edges[(parent['service'],r['service'])][i].append({**parent,'server_span_id':r['span_id']})
 events=[];stats=[]
 for (src,dst),bins in sorted(edges.items()):
  base=[r for i,items in bins.items() if i<10 for r in items]
  base_p95=p95([r['duration_ms'] for r in base]);base_err=sum(r['is_error'] for r in base)/len(base) if base else None
  mean_count=len(base)/10
  for i in range(60):
   items=bins[i];lat=p95([r['duration_ms'] for r in items]);errs=[r for r in items if r['is_error']]
   stats.append({'source':src,'destination':dst,'minute':i,'client_server_identity_pairs':len(items),'client_p95_ms':lat,'client_errors':len(errs),'baseline_p95_ms':base_p95,'baseline_mean_count_per_minute':mean_count})
   if i<10 or len(base)<30:continue
   kinds=[]
   if len(items)>=10 and lat>=max(2*base_p95,base_p95+10):kinds.append('CALL_EDGE_CLIENT_LATENCY_INCREASE')
   if len(errs)>=3 and len(errs)/len(items)>base_err+.05:kinds.append('CALL_EDGE_CLIENT_ERROR_BURST')
   # Count loss is ambiguous between business effects and missing server evidence;
   # retain it as an audit statistic, never assert a fault from absence alone.
   for kind in kinds:
    selected=errs if 'ERROR' in kind else [r for r in items if r['duration_ms']>=max(2*base_p95,base_p95+10)]
    events.append({'anomaly_id':'EDGE-'+str(len(events)+1).zfill(4),'entity_type':'CALL_EDGE','entity_id':src+' -> '+dst,'anomaly_type':kind,
      'start_time':utc(min(r['start_time'] for r in selected)),'end_time':utc(max(r['start_time'] for r in selected)),
      'modalities':['Traces'],'confidence':'MEDIUM','review_status':'DRAFT_PENDING_HUMAN_REVIEW',
      'boundary_semantics':'first/last qualifying observed caller span start; no injection schedule read',
      'evidence':{'modality':'Traces','file':'traces.parquet','identity_join':'actual server CHILD_OF -> actual client Span ID within same Trace ID','baseline_p95_ms':base_p95,
        'examples':[{'trace_id':r['trace_id'],'client_span_id':r['span_id'],'server_span_id':r['server_span_id'],'client_duration_ms':r['duration_ms']} for r in selected[:10]],
        'minute':i,'paired_call_count':len(items),'error_count':len(errs),'client_p95_ms':lat}})
 base=json.loads(a.base.read_text());all_events=base['events']+events;merged=[]
 for e in sorted(all_events,key=lambda e:(e['entity_type'],e['entity_id'],e['start_time'])):
  g=next((v for v in reversed(merged) if v['entity_type']==e['entity_type'] and v['entity_id']==e['entity_id'] and iso_ns(e['start_time'])<=iso_ns(v['end_time'])+30*10**9),None)
  if g is None:
   g={k:e[k] for k in ('entity_type','entity_id','start_time','end_time','confidence','review_status')};g.update(anomaly_id='EVENT-'+str(len(merged)+1).zfill(4),anomaly_type=[],modalities=[],evidence=[],candidate_ids=[]);merged.append(g)
  g['end_time']=max(g['end_time'],e['end_time']);g['candidate_ids'].append(e['anomaly_id']);g['anomaly_type']=sorted(set(g['anomaly_type']+[e['anomaly_type']]));g['modalities']=sorted(set(g['modalities']+e['modalities']));g['evidence'].append(e)
 result={**base,'events':all_events,'candidate_count':len(all_events),'merged_events':merged,'merged_event_count':len(merged),
  'edge_evidence':{'paired_client_spans':len(joined),'unpaired_client_spans':len(client)-len(joined),'observed_edges':len(edges),'minute_statistics':stats},
  'analysis_version_sha256':sha(Path(__file__)),'exploratory_annotations_not_quality_gate':True,'no_causal_attribution_claimed':True,
  'limitations':['Anomalies are observed symptoms, not injection labels.','Absent server spans cannot be assigned to a failed destination from the injection plan.','Latency and error flags require human review for ordinary load variation.','Parent missing at public time-window boundaries remains unresolved.']}
 with a.output.open('x') as f:json.dump(result,f,indent=2,ensure_ascii=False)
 print(json.dumps({'status':result['status'],'candidates':len(all_events),'events':len(merged),'edge_candidates':len(events)}))
if __name__=='__main__':main()
