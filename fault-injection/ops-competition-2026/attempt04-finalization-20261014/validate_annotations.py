"""Resolve and normalize draft citations against Public only; no detector threshold changes."""
import argparse,json,sys
from pathlib import Path
from datetime import datetime,timezone
from collections import defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'code/scripts'))
from pilot_annotations import rows,error
from pilot_exchange_schema import iso_ns
from pilot_export import sha

def utc(ns):return datetime.fromtimestamp(ns//10**9,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+f'.{ns%10**9:09d}Z'
def main():
 p=argparse.ArgumentParser();p.add_argument('--public',type=Path,required=True);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--audit',type=Path,required=True);a=p.parse_args()
 if a.output.exists() or a.audit.exists():raise RuntimeError('NO_OVERWRITE')
 draft=json.loads(a.input.read_text());events=draft['events'];support={e['anomaly_id']:{'count':0,'min':None,'max':None,'examples':[]} for e in events}
 wanted=defaultdict(list);edge_ids=set();edge_rows={}
 for e in events:
  ev=e['evidence'];kind=e['entity_type']
  if kind=='CALL_EDGE':
   for x in ev['examples']:
    for name in ('client_span_id','server_span_id'):edge_ids.add((x['trace_id'].lower(),x[name].lower()))
  elif ev['modality']=='Traces':
   f=ev['filter'];wanted[(f['service'],f['operation_name'],f['span.kind'])].append(e)
 def in_window(e,t):return iso_ns(e['start_time'])-1000<=t<=iso_ns(e['end_time'])+1000
 def add(e,t,example):
  s=support[e['anomaly_id']];s['count']+=1;s['min']=t if s['min'] is None else min(t,s['min']);s['max']=t if s['max'] is None else max(t,s['max'])
  if len(s['examples'])<10:s['examples'].append(example)
 for r in rows(a.public/'traces.parquet',['trace_id','span_id','parent_span_id','service','operation_name','start_time','duration_ms','attributes']):
  k=(r['trace_id'].lower(),r['span_id'].lower())
  if k in edge_ids:edge_rows[k]=r
  attrs=json.loads(r['attributes'] or '{}')
  for e in wanted.get((r['service'],r['operation_name'],attrs.get('span.kind','UNKNOWN')),[]):
   if not in_window(e,r['start_time']):continue
   ev=e['evidence'];threshold=max(2*ev['baseline_p95_ms'],ev['baseline_p95_ms']+10,2*ev.get('independent_no_fault_reference',{}).get('p95_ms',0))
   if (e['anomaly_type']=='TRACE_ERROR_BURST' and error(attrs)) or (e['anomaly_type']=='TRACE_LATENCY_INCREASE' and r['duration_ms']>=threshold):add(e,r['start_time'],{'trace_id':r['trace_id'],'span_id':r['span_id'],'duration_ms':r['duration_ms'],'observed_utc':utc(r['start_time'])})
 memory=[e for e in events if e['evidence']['modality']=='Metrics'];logs=[e for e in events if e['evidence']['modality']=='Logs']
 for r in rows(a.public/'metrics.parquet',['timestamp','metric_name','value','labels']):
  labels=json.loads(r['labels'])
  for e in memory:
   ev=e['evidence']
   if r['metric_name']==ev['metric'] and all(labels.get(k)==v for k,v in ev['labels_filter'].items()) and in_window(e,r['timestamp']) and r['value']>=ev['baseline_median_bytes']+ev['increase_threshold_bytes']:add(e,r['timestamp'],{'timestamp':utc(r['timestamp']),'value':r['value'],'metric_name':r['metric_name'],'labels':labels})
 for r in rows(a.public/'logs.parquet',['timestamp','service','severity','body','trace_id','span_id']):
  for e in logs:
   ev=e['evidence']
   if r['service']==ev['filter']['service'] and str(r['severity']).upper() in ev['filter']['severity'] and in_window(e,r['timestamp']):add(e,r['timestamp'],{'timestamp':utc(r['timestamp']),'body':r['body'],'trace_id':r['trace_id'],'span_id':r['span_id'],'severity':r['severity']})
 edge_ok=True
 for e in events:
  if e['entity_type']!='CALL_EDGE':continue
  for ex in e['evidence']['examples']:
   client=edge_rows.get((ex['trace_id'].lower(),ex['client_span_id'].lower()));server=edge_rows.get((ex['trace_id'].lower(),ex['server_span_id'].lower()))
   if not client or not server or server['parent_span_id'].lower()!=client['span_id'].lower() or client['service']+' -> '+server['service']!=e['entity_id'] or not in_window(e,client['start_time']):edge_ok=False;continue
   add(e,client['start_time'],{**ex,'observed_utc':utc(client['start_time'])})
 failed=[k for k,v in support.items() if not v['count']]
 if failed or not edge_ok:raise RuntimeError('ANNOTATION_PUBLIC_EVIDENCE_UNRESOLVED:'+str(failed))
 for e in events:
  s=support[e['anomaly_id']];e['evidence']['examples']=s['examples'];e['evidence']['public_evidence_resolution']='PASS';e['evidence']['verified_support_count']=s['count'];e['evidence']['support_count_scope']='cited independent identity pairs' if e['entity_type']=='CALL_EDGE' else 'all matching public records within candidate interval'
  # Keep detector intervals; timestamp representation is explicit. No injection timing read.
  e['evidence']['verified_sample_bounds']={'first':utc(s['min']),'last':utc(s['max'])};e['evidence']['time_matching_tolerance_ns']=1000
 merged=[]
 for e in sorted(events,key=lambda e:(e['entity_type'],e['entity_id'],e['start_time'])):
  g=next((v for v in reversed(merged) if v['entity_type']==e['entity_type'] and v['entity_id']==e['entity_id'] and iso_ns(e['start_time'])<=iso_ns(v['end_time'])+30*10**9),None)
  if g is None:
   g={k:e[k] for k in ('entity_type','entity_id','start_time','end_time','confidence','review_status')};g.update(anomaly_id='EVENT-'+str(len(merged)+1).zfill(4),anomaly_type='OBSERVED_ANOMALY_EVENT',anomaly_types=[],modalities=[],evidence=[],candidate_ids=[]);merged.append(g)
  g['end_time']=max(g['end_time'],e['end_time']);g['candidate_ids'].append(e['anomaly_id']);g['anomaly_types']=sorted(set(g['anomaly_types']+[e['anomaly_type']]));g['modalities']=sorted(set(g['modalities']+e['modalities']));g['evidence'].append(e)
 draft.update(events=events,merged_events=merged,merged_event_count=len(merged),citation_validation='PASS',citation_validation_source_sha256=sha(Path(__file__)),pre_citation_draft_sha256=sha(a.input),citation_policy='Each example comes from the corresponding public event; original exploratory draft preserved; detection rules unchanged; <=1us tolerance for prior datetime rendering only')
 audit={'status':'ALL_ANOMALY_PUBLIC_EVIDENCE_REFERENCES_VERIFIED','candidates':len(events),'merged_events':len(merged),'all_events_have_public_support':True,'edge_identity_pairs_verified':edge_ok,'input_sha256':sha(a.input),'source_public_manifest_sha256':sha(a.public/'manifest.json'),'private_injection_or_gt_read':False,'detector_thresholds_changed':False,'future_scoring_gt_approved':False}
 for path,value in [(a.output,draft),(a.audit,audit)]:
  with path.open('x') as f:json.dump(value,f,indent=2,ensure_ascii=False)
 print(json.dumps(audit))
if __name__=='__main__':main()
