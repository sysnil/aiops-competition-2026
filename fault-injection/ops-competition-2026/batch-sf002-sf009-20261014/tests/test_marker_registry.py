import unittest,tempfile,json,sys,copy
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_registry import load_bundle,BACKEND,sha,verify_timer_inventory,expected_argv
from marker_worker import validate_lease,onset,arm
utc=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
class Registry(unittest.TestCase):
 def bundle(self,n=6):
  root=Path(self.tmp.name);pp=root/'plan.json';lp=root/'lock.json';mp=root/'manifest.json';run='B8-MARKER-UNIT'
  events=[{'run_id':run,'action_id':run+f'-E{i+1:02d}','worker':'k8s-worker'+str(i%3+1),'unit':'ops-b8-'+(run+f'-E{i+1:02d}').lower(),'backend':BACKEND,'execute':'WRITE_LOCAL_TIME_MARKER','revoke':'REVOKE_AND_CANCEL_MARKER','targets':[],'registration_file':str(pp),'offset_seconds':45*(i+1),'planned_utc':utc(1000+45*(i+1)),'duration_seconds':3,'onset_tolerance_seconds':2,'runtime':str(root/'worker-runtime'/str(i))} for i in range(n)]
  p={'id':run,'backend':BACKEND,'formal_case':False,'business_fault_permission':False,'start_utc':utc(1000),'actions':events,'controller_unit':'control','observer_unit':'observe'};pp.write_text(json.dumps(p));lp.write_text(json.dumps({'run_id':run,'plan_sha256':sha(pp),'actions':events}));m={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':run,'backend':BACKEND,'plan_path':str(pp),'plan_sha256':sha(pp),'schedule_lock_path':str(lp),'schedule_lock_sha256':sha(lp),'actions':events,'controller_unit':'control','observer_unit':'observe'};mp.write_text(json.dumps(m));return root,mp,m,p
 def setUp(self):self.tmp=tempfile.TemporaryDirectory()
 def tearDown(self):self.tmp.cleanup()
 def test_counts_two_through_six(self):
  for n in range(2,7):
   r,f,m,p=self.bundle(n);self.assertEqual(len(load_bundle(f,sha(f),root=r)[0]['actions']),n)
 def test_out_of_range_count(self):
  for n in (1,7):
   r,f,m,p=self.bundle(n)
   with self.assertRaises(ValueError):load_bundle(f,sha(f),root=r)
 def test_wrong_hash(self):
  r,f,m,p=self.bundle()
  with self.assertRaises(ValueError):load_bundle(f,'0'*64,root=r)
 def test_real_backend_rejected(self):
  r,f,m,p=self.bundle();m['backend']='REAL_FAULT';f.write_text(json.dumps(m))
  with self.assertRaises(ValueError):load_bundle(f,sha(f),root=r)
 def test_three_worker_timer_inventory(self):
  r,f,m,p=self.bundle()
  for worker in ('k8s-worker1','k8s-worker2','k8s-worker3'):
   props={a['unit']+'.timer':{'worker':worker,'active':'active','service_unit':a['unit']+'.service','argv':expected_argv(f,sha(f),a['action_id'],r),'planned_epoch':1000+a['offset_seconds'],'accuracy_usec':'10ms','random_delay_usec':'0','persistent':'no'} for a in m['actions'] if a['worker']==worker}
   self.assertTrue(verify_timer_inventory(m,p,f,sha(f),worker,set(props),props,r)['pass'])
   with self.assertRaises(ValueError):verify_timer_inventory(m,p,f,sha(f),worker,set(props)|{'old.timer'},props,r)
 def test_onset_over_two_rejected(self):
  r,f,m,p=self.bundle();self.assertFalse(onset(m['actions'][0],utc(1047.1),{'worker_minus_master_seconds':0,'uncertainty_seconds':.02})['pass'])
 def test_cancelled_cannot_rearm(self):
  r,f,m,p=self.bundle();state=r/'cancelled';state.mkdir();(state/'REVOKED.json').write_text('{}')
  with patch('marker_worker.bind',return_value=(m,p,m['actions'][0],state)),patch('marker_worker.subprocess.run') as command:
   with self.assertRaises(RuntimeError):arm(None)
   command.assert_not_called()
 def test_real_permission_rejected(self):
  r,f,m,p=self.bundle()
  with self.assertRaises(RuntimeError):validate_lease(m,sha(f),m['actions'][0],{'permission':'BOUND_QUALIFIED_FAULT'},1045)
 def test_worker_source_has_no_business_adapter(self):
  s=(Path(__file__).resolve().parents[1]/'scripts/marker_worker.py').read_text()
  for forbidden in ('competition_adapter','SIGSTOP','resource_memory_holder','tc,','kubectl'):self.assertNotIn(forbidden,s)
if __name__=='__main__':unittest.main()
