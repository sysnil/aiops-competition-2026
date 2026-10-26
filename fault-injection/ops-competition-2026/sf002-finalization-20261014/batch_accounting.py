"""Publish a new revision of batch accounting; preserve prior planning and all raw attempts."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
BASE=Path('/root/ops-competition-2026')
D=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009')
O=D/'revisions/storage20-20261014'
def read(p):return json.loads(p.read_text())
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2,ensure_ascii=False)
def main():
 old=BASE/'batch-sf002-sf009-20261014/results'
 cases=read(old/'case-results.json');capabilities=read(old/'fault-capability-matrix.json')
 terminal=read(D/'runtime/SF-PILOT-02-ATTEMPT-01/controller-terminal.json')
 quality=read(D/'SF-002/capture-and-effects-assessment.json') if (D/'SF-002/capture-and-effects-assessment.json').exists() else None
 validation=read(D/'SF-002/private/fault-validation-report-v2.json') if (D/'SF-002/private/fault-validation-report-v2.json').exists() else None
 export=read(D/'SF-002/reports/public-readback-verification.json') if (D/'SF-002/reports/public-readback-verification.json').exists() else None
 index=Path('/data/stage5-v3/ops-competition-archive-index/SF-PILOT-02-ATTEMPT-01')
 archived=all((index/(h+'-final-archive-result.json')).is_file() for h in ('w2','w3'))
 if archived:
  archived=all(r['status']=='COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' for h in ('w2','w3') for r in read(index/(h+'-final-archive-result.json')).values())
 ready=bool(terminal['status']=='FORMAL_EXECUTION_COMPLETE' and quality and quality['status']=='CASE_CAPTURE_AND_EFFECTS_PASS' and export and export['status']=='PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS' and archived)
 for row in cases:
  row['additional_blockers']=[b for b in row['additional_blockers'] if b!='STORAGE_BLOCKED']
  row['storage_note']='20GB policy permits observed SF-002 peak; each later case still requires its own fresh budget'
  if row['case_id']=='SF-002':
   row.update(status='SF002_READY_FOR_IMPORT' if ready else 'SF002_REVIEW' if terminal['status']=='FORMAL_EXECUTION_COMPLETE' else 'SF002_BLOCKED',actual_faults=len(terminal.get('faults',{})),preregistered=True,production_started=True,
      safety_recovery='PASS' if terminal['status']=='FORMAL_EXECUTION_COMPLETE' else 'SEE_TERMINAL',telemetry_quality=quality['status'] if quality else 'NOT_COMPLETED',public='VERIFIED' if export else 'NOT_EXPORTED',gt='DRAFT_PENDING_HUMAN_REVIEW' if validation else 'NOT_CREATED',source_run='SF-PILOT-02-ATTEMPT-01')
  else:row['status']='MECHANISM_UNAVAILABLE' if 'MECHANISM_UNAVAILABLE' in row['additional_blockers'] else 'SAFETY_BLOCKED'
 for row in capabilities:
  row['blockers']=[b for b in row['blockers'] if b!='STORAGE_BLOCKED']
  if validation:
   match=next((v for v in validation['events'] if v['fault_type']==row['fault_type']),None)
   if match:
    row.update(actual_executions=1,batch_conclusion=match['mechanism_validation_status'],engineering_status='QUALIFIED_EXISTING_ADAPTER_REUSED',blockers=[],injection_success=match['checks']['Injection Success'],public_observable=match['checks']['Observability'],recovery=match['checks']['Recovery'],user_impact=match['user_impact'],actual_evidence=str(D/'SF-002/private/fault-validation-report-v2.json'))
 result={'status':'BATCH_8_PARTIAL' if ready else 'BATCH_8_BLOCKED','at_utc':datetime.now(timezone.utc).isoformat(),'formal_cases_started':1,'ready_cases':int(ready),'remaining_cases':7,'actual_injections':len(terminal.get('faults',{})),'disk_floor_bytes':20000000000,'no_substituted_mechanisms':True,'case_results':cases,'fault_capabilities':capabilities,'no_contest_server_access':True}
 save(O/'batch-decision.json',result);save(O/'case-results.json',cases);save(O/'fault-capability-matrix.json',capabilities)
 print(json.dumps({k:result[k] for k in ('status','formal_cases_started','ready_cases','remaining_cases','actual_injections')}))
if __name__=='__main__':main()
