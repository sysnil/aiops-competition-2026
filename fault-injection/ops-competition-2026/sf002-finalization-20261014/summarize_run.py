"""Actual immutable execution accounting; no GT generation and no injection capability."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime
import json,hashlib,shutil
ROOT=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002')
CONTROL=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/runtime/SF-PILOT-02-ATTEMPT-01')
CODE=Path('/root/ops-competition-2026/sf002-production-20261014')
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 terminal=read(CONTROL/'controller-terminal.json');assessment=read(ROOT/'capture-and-effects-assessment.json');plan=read(CODE/'plans/SF-PILOT-02-ATTEMPT-01/run-plan.json')
 rows=Counter();kinds=Counter();first={};last={};maxmem=0;mindisk=None;firsttime=None;lasttime=None;resource=[];maxraw=0
 with (ROOT/'safety.jsonl').open() as f:
  for line in f:
   x=json.loads(line);co=x['collector'];maxmem=max(maxmem,co['working_set_bytes']);maxraw=max(maxraw,x.get('storage_budget',{}).get('raw_control_bytes',0));rows['safety_samples']+=1
   root=x['disk']['root_free_bytes'];mindisk=root if mindisk is None else min(mindisk,root)
   firsttime=firsttime or x['at_utc'];lasttime=x['at_utc']
   for m in co['metrics']:
    name=m['metric']['__name__']
    if any(t in name for t in ('refused','failed','retry','sent_spans','accepted_spans','queue_size','queue_capacity')):
     key=json.dumps(m['metric'],sort_keys=True);v=float(m['value'][1]);first.setdefault(key,v);last[key]=v
   resource.append({'time':x['at_utc'],'collector_working_set_bytes':co['working_set_bytes'],'root_free_bytes':root,'data_free_bytes':x['disk']['data_free_bytes']})
 delta=[{'labels':json.loads(k),'first':v,'last':last[k],'delta':last[k]-v} for k,v in first.items()]
 sessions=Counter()
 for line in (ROOT/'state-machine/sessions.jsonl').read_text().splitlines():
  x=json.loads(line);sessions[str(x.get('failure'))]+=1
 for stream,file in [('business','state-machine/request-events-live.jsonl'),('controls','controls/control-events-live.jsonl')]:
  with (ROOT/file).open() as f:
   for line in f:
    x=json.loads(line);rows[stream+'_requests']+=1
    if not x['success']:kinds[stream+':'+str(x.get('error_type'))+':'+str(x.get('status_code'))]+=1
 chunks=[]
 for p in sorted((ROOT/'capture').glob('*/quality.json')):
  q=read(p);chunks.append({k:q[k] for k in ('chunk','status','first_spans','plus60_spans','final_spans','audit_spans','logs','completion_lag_seconds','expected_prefix_requests','matched_prefix_requests','unmatched_request_ids')})
 receipts={p.stem:read(p) for p in sorted((ROOT/'diagnostics').glob('*-collector-log-audit.json'))}
 result={'status':'ACTUAL_EXECUTION_ACCOUNTED','controller_status':terminal['status'],'case_quality':assessment['status'],'requests':dict(rows),'failed_request_classification':dict(kinds),'session_terminal_classification':dict(sessions),
  'collector_working_set_peak_bytes':maxmem,'collector_counter_deltas':delta,'missing_optional_exporter_counters':'UNKNOWN_NOT_ZERO','collector_log_audits':receipts,
  'first_safety_sample':firsttime,'last_safety_sample':lasttime,'minimum_master_root_free_bytes':mindisk,'maximum_observed_raw_control_bytes':maxraw,'storage_budget':plan['storage_budget'],
  'chunks':chunks,'onsets':{p.stem:read(p) for p in CONTROL.glob('*-onset.json')},'injections':terminal['faults'],'source_sha256_unchanged':all(sha(Path(p))==v for p,v in plan['source_sha256'].items()),
  'gt_generation':'NOT_PERFORMED_BY_THIS_SCRIPT','public_export':'SEPARATE_QUALITY_GATED_STEP'}
 out=ROOT/'reports';out.mkdir(exist_ok=True)
 for name,value in [('execution-accounting.json',result),('collector-resource-series.json',resource)]:
  with (out/name).open('x') as f:json.dump(value,f,indent=2)
 print(json.dumps({'case_quality':assessment['status'],'max_collector_mib':maxmem/1024**2,'requests':dict(rows),'errors':dict(kinds),'sessions':dict(sessions),'all_chunks_pass':all(q['status']=='CHUNK_QUALITY_PASS' for q in chunks)}))
if __name__=='__main__':main()
