import unittest,sys,copy,json,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from batch_contract import *
class Contracts(unittest.TestCase):
 def base_event(self):
  v={'executed':True,'safety_violation':False}
  for k in ['Injection Success','Target Correctness','Fault Intensity','Observability','Telemetry Quality','User Impact','Recovery']:
   v[k]={'status':'PASS','evidence':['public/metrics.parquet#t=1'] if k=='Observability' else ['private/real-state-proof.json']}
  v['User Impact']['outcome']='USER_IMPACT_CONFIRMED';return v
 def quality(self):return {k:True for k in ['full_hour','telemetry','public_schema','public_private_boundary','archive','gt_traceability','final_safety']}
 def test_all_requested_combinations_unchanged(self):
  self.assertEqual(sum(map(len,CASES.values())),32);self.assertEqual(len(DIMENSIONS),14)
  root=Path(__file__).resolve().parents[1]
  for p in (root/'plans').glob('*-candidate.json'):self.assertTrue(validate_case_config(json.loads(p.read_text())))
 def test_unsupported_names_not_substituted(self):
  self.assertNotIn('CPUQuota',DIMENSIONS);self.assertNotEqual(DIMENSIONS['HTTP ReqDelay'],DIMENSIONS['HTTP RespDelay'])
 def test_original_two_fault_time_contract(self):
  p=json.loads((Path(__file__).resolve().parents[1]/'plans/SF-002-candidate.json').read_text())
  self.assertEqual(time_plan(p['fault_events'])['status'],'TIME_PLAN_PASS')
  self.assertEqual(capture_offsets(p['fault_events']),[0,300,600,1200,1500,1800,2100,2400,2700,3000,3300,3600])
 def test_unknown_duration_never_assumed_short(self):self.assertEqual(time_plan([{'fault_event_id':'X','start_seconds':720}])['status'],'TIME_PLAN_PENDING_MECHANISM_CONTRACT')
 def test_early_template_rejected(self):
  ev=[{'fault_event_id':'X','start_seconds':420,'duration_seconds':300,'recovery_seconds':180,'precheck_seconds':105,'recovery_check_seconds':45}]
  self.assertEqual(time_plan(ev)['status'],'TIME_PLAN_BLOCKED')
 def test_recovery_precheck_overlap_rejected(self):
  e={'fault_event_id':'X','start_seconds':720,'duration_seconds':300,'recovery_seconds':240,'precheck_seconds':105,'recovery_check_seconds':45}
  self.assertEqual(time_plan([e,{**e,'start_seconds':1320}])['status'],'TIME_PLAN_BLOCKED')
 def test_six_300_second_events_do_not_fit(self):
  ev=[{'fault_event_id':str(i),'start_seconds':720+i*720,'duration_seconds':300,'recovery_seconds':240,'precheck_seconds':105,'recovery_check_seconds':45} for i in range(6)]
  self.assertEqual(time_plan(ev)['status'],'TIME_PLAN_BLOCKED')
 def test_space_floor_exact_and_one_byte_below(self):
  free={'master_root':46000000000+64*1024**2,'master_data':98*G,'w2_data':50000000000+31*G,'w3_data':50000000000+29*G}
  self.assertEqual(capacity(free)['status'],'STORAGE_PASS');free['w2_data']-=1;self.assertEqual(capacity(free)['status'],'STORAGE_BLOCKED')
 def test_current_storage_is_blocked(self):
  r=json.loads((Path(__file__).resolve().parents[1]/'results/storage-admission.json').read_text());self.assertEqual(r['status'],'STORAGE_BLOCKED');self.assertGreater(r['checks']['w2_data']['shortfall_bytes'],0)
 def test_marker_cannot_authorize_production(self):
  self.assertFalse(activate_gate({'status':'STORAGE_PASS'},['ENGINEERING_SAFETY_PASS'],{'status':'TIME_PLAN_PASS'},'SIX_EVENT_MARKER_SCHEDULER_PASS',True,True)['allowed'])
 def test_no_mechanism_proofs_cannot_authorize_case(self):
  self.assertFalse(activate_gate({'status':'STORAGE_PASS'},[],{'status':'TIME_PLAN_PASS'},'FULL_PRODUCTION_PIPELINE_VALIDATED',True,True)['allowed'])
 def test_missing_health_blocks(self):self.assertFalse(activate_gate({'status':'STORAGE_PASS'},[],{'status':'TIME_PLAN_PASS'},'FULL_PRODUCTION_PIPELINE_VALIDATED',False,True)['allowed'])
 def test_grpc_cannot_be_http(self):
  with self.assertRaises(ValueError):http_scope({'application_protocol':'HTTP','content_type':'application/grpc','public_trace_reference':'x','verified_route':True,'control_routes_excluded':True})
 def test_no_fault_record_is_not_success(self):self.assertEqual(decide_event({'executed':False})['status'],'NOT_EXECUTED')
 def test_no_command_returncode_shortcut(self):
  v=self.base_event();v['Injection Success']['evidence']=[]
  with self.assertRaises(ValueError):decide_event(v)
 def test_private_observability_rejected(self):
  for ref in ['private/gt.json','public/../private/gt.json','/public/metrics.parquet','public/anomaly-gt-draft.json']:
   v=self.base_event();v['Observability']['evidence']=[ref]
   with self.assertRaises(ValueError):decide_event(v)
 def test_observable_normal(self):self.assertEqual(decide_event(self.base_event())['status'],'SUPPORTED_OBSERVABLE')
 def test_no_user_errors_can_still_be_supported(self):
  v=self.base_event();v['User Impact'].update(status='FAIL',outcome='NO_USER_IMPACT_OBSERVED');self.assertEqual(decide_event(v)['status'],'SUPPORTED_LOW_IMPACT')
 def test_unknown_impact_not_none(self):
  v=self.base_event();v['User Impact']['status']='UNKNOWN';self.assertEqual(decide_event(v)['status'],'REVIEW_REQUIRED')
 def test_bad_impact_measurement_not_no_impact(self):
  v=self.base_event();v['User Impact'].update(status='FAIL',outcome='MEASUREMENT_FAILED');self.assertEqual(decide_event(v)['status'],'REVIEW_REQUIRED')
 def test_recovery_failure_stops_batch(self):
  v=self.base_event();v['Recovery']['status']='FAIL';self.assertTrue(decide_event(v)['batch_stop'])
 def test_unknown_recovery_stops_batch(self):
  v=self.base_event();v['Recovery']['status']='UNKNOWN';self.assertTrue(decide_event(v)['batch_stop'])
 def test_telemetry_failure_independent_of_user_impact(self):
  v=self.base_event();v['Telemetry Quality']['status']='FAIL';self.assertEqual(decide_event(v)['status'],'TELEMETRY_BLOCKED')
 def test_four_of_five_cannot_pass(self):
  p={'fault_events':[{'fault_event_id':str(i)} for i in range(5)]}
  ev=[{'fault_event_id':str(i),'status':'SUPPORTED_OBSERVABLE','batch_stop':False} for i in range(5)];ev[3]['status']='INJECTION_INEFFECTIVE'
  self.assertEqual(decide_case(p,ev,self.quality()),'CASE_REVIEW_REQUIRED')
  with self.assertRaises(ValueError):decide_case(p,ev[:4],self.quality())
 def test_empty_quality_cannot_pass(self):
  with self.assertRaises(ValueError):decide_case({'fault_events':[]},[],{})
 def test_state_machine_terminates_and_no_rearm(self):
  m=EventMachine('X')
  for s in ['PREFLIGHT_PASSED','AUTHORIZED','STARTED','ACTION_COMPLETED','RECOVERY_CONFIRMED']:m.move(s,'evidence')
  with self.assertRaises(ValueError):m.move('AUTHORIZED','evidence')
 def test_abort_before_injection(self):
  m=EventMachine('X');m.move('ABORTED_NOT_EXECUTED','health fail')
  with self.assertRaises(ValueError):m.move('STARTED','evidence')
 def test_empty_case_cannot_pass_complete_quality(self):
  with self.assertRaises(ValueError):decide_case({'fault_events':[]},[],self.quality())
 def test_unknown_http_type_not_assumed_safe(self):
  with self.assertRaises(ValueError):http_scope({'application_protocol':'HTTP','public_trace_reference':'x','verified_route':True,'control_routes_excluded':True})
 def test_execution_string_not_a_boolean(self):
  with self.assertRaises(ValueError):decide_event({'executed':'false'})
 def test_all_new_sources_compile(self):
  for p in Path(__file__).resolve().parents[1].glob('scripts/*.py'):compile(p.read_text(),str(p),'exec')
if __name__=='__main__':unittest.main()
