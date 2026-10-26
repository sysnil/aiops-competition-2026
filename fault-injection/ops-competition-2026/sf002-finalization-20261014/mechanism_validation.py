"""Post-run evidence accounting. No commands, injection, selection or parameter tuning."""
import json,statistics
from pathlib import Path
from datetime import datetime
R=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002')
C=R.parent/'runtime/SF-PILOT-02-ATTEMPT-01'
P=Path('/root/ops-competition-2026/sf002-production-20261014/plans/SF-PILOT-02-ATTEMPT-01/run-plan.json')
def read(p):return json.loads(p.read_text())
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def main():
 plan=read(P);terminal=read(C/'controller-terminal.json');assessment=read(R/'capture-and-effects-assessment.json')
 requests=[]
 for name in ('state-machine/request-events-live.jsonl','controls/control-events-live.jsonl'):
  requests += [json.loads(l) for l in (R/name).read_text().splitlines()]
 rows=[]
 for a in plan['actions']:
  key=a['action_id'];injection=terminal.get('faults',{}).get(key);onset=terminal.get('onsets',{}).get(key)
  layer=a['mechanism_id'];effect=assessment['service_effect' if layer=='SERVICE' else 'pod_effect']
  lo=stamp(a['planned_utc']);hi=lo+a['duration_seconds']
  impact={}
  for phase,start,end in [('before',lo-300,lo),('during',lo,hi),('after',hi,hi+300)]:
   rs=[x for x in requests if start<=stamp(x['request_started_utc'])<end]
   impact[phase]={'request_count':len(rs),'failed':sum(not x['success'] for x in rs),'successful':sum(bool(x['success']) for x in rs),'scope':'all observed ordinary business and control requests; not inferred per-Pod attribution'}
  checks={'Injection Success':bool(injection and injection['status']=='COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED'),
   'Target Correctness':bool(onset and onset.get('actual_target_states') and len(onset['actual_target_states'])==len(a['targets']) and all(any(all(str(actual[k])==str(target[k]) for k in ('pid','start_ticks','cgroup')) for actual in onset['actual_target_states']) for target in a['targets'])),
   'Fault Intensity':bool(injection and abs(injection['duration_seconds']-300)<=2),
   'Observability':effect['pass'],
   'Telemetry Quality':assessment['checks']['all_registered_chunks_pass'] and assessment['checks']['all_expected_requests_explained'] and assessment['checks']['collector_refused_no_increment'],
   'Recovery':bool(injection and injection.get('cleanup',{}).get('status')=='COMPETITION_CLEANUP_ACTIONS_VERIFIED') and read(R/('recovery-'+layer.lower()+'.json'))['status']=='PUBLIC_RECOVERY_PASS',
   'Interference':assessment['checks']['full_rate_controls_successful'] and assessment['checks']['final_actual_safety'],
   'Evidence Quality':bool(effect['pass'] and assessment['checks']['all_expected_requests_explained'])}
  rows.append({'case_id':'SF-002','fault_event_id':a['fault_event_id'],'action_id':key,'fault_category':a['fault_category'],'fault_type':a['fault_type'],'target_layer':layer,'targets':a['targets'],
    'planned_start':a['planned_utc'],'actual_start':injection.get('actual_start_utc') if injection else None,'actual_end':injection.get('actual_end_utc') if injection else None,
    'requested_parameters':{'duration_seconds':300,**({'allocation_bytes':33554432} if layer=='POD' else {'scope':'both registered recommendation replicas'})},
    'actual_duration_seconds':injection.get('duration_seconds') if injection else None,'onset':onset,'cleanup':injection.get('cleanup') if injection else None,
    'checks':checks,'user_impact':impact,'public_effect':effect,
    'mechanism_validation_status':'SUPPORTED_OBSERVABLE' if all(checks.values()) else 'REVIEW_REQUIRED',
    'memory_interpretation':'32 MiB requested allocation; actual container metric includes helper overhead, not equated to exact allocation' if layer=='POD' else None,
    'limitations':['ordinary control stability covers registered controls, not proof of zero effect on every non-target entity','zero failed requests is not proof of no latency impact','no RCA prediction used']})
 out=R/'private/fault-validation-report.json';out.parent.mkdir(exist_ok=True,mode=0o700)
 with out.open('x') as f:json.dump({'case_id':'SF-002','events':rows,'source_assessment':str(R/'capture-and-effects-assessment.json')},f,indent=2)
 print(json.dumps([{'event':r['fault_event_id'],'status':r['mechanism_validation_status'],'checks':r['checks']} for r in rows]))
if __name__=='__main__':main()
