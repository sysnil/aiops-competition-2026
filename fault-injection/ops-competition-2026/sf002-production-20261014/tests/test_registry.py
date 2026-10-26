import sys,json,copy,tempfile,unittest,hashlib,ast
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_registry import BACKEND,load_bundle,verify_timer_inventory,expected_argv,sha
from fault_worker import validate_lease,onset,arm
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.run='SF-PILOT-02-ATTEMPT-01';self.pp=self.root/'arbitrary-plan.json';self.lp=self.root/'not-a-guessed-lock.json';self.mp=self.root/'explicit-manifest.json'
        self.actions=[]
        for layer,host,offset in [('SERVICE','k8s-worker3',720),('POD','k8s-worker2',2100)]:
            ident=self.run+'-'+layer;self.actions.append({'run_id':self.run,'action_id':ident,'mechanism_id':layer,'worker':host,'targets':[{'uid':'public-uid-'+layer+str(i),'node':host,'service':'recommendation' if layer=='SERVICE' else 'frontend'} for i in range(2 if layer=='SERVICE' else 1)],
              'planned_utc':utc(1000+offset),'offset_seconds':offset,'duration_seconds':300,'allocation_bytes':33554432,'onset_tolerance_seconds':2,
              'unit':'ops-sf002-'+ident.lower(),'runtime':str(self.root/'worker-runtime'/ident),'registration_file':str(self.pp),
              'backend':BACKEND,'execute':'RUN_PINNED_QUALIFIED_ADAPTER','revoke':'REVOKE_STOP_AND_PINNED_CLEANUP'})
        for a in self.actions:
            reg=self.root/(a['action_id']+'.json');reg.write_text(json.dumps({'id':a['action_id'],'targets':a['targets'],'duration_seconds':300,'layer':a['mechanism_id'],'host':a['worker']}))
            a.update(qualified_registration=str(reg),qualified_registration_sha256=sha(reg))
        self.plan={'id':self.run,'backend':BACKEND,'formal_case':True,'duration_seconds':3600,'authorization_scope':'ONE_SF002_SERVICE_POD_QUALIFIED_ONLY','start_utc':utc(1000),'actions':self.actions,'controller_unit':'controller','observer_unit':'observer'}
        self.pp.write_text(json.dumps(self.plan));self.lock={'run_id':self.run,'plan_sha256':sha(self.pp),'actions':self.actions};self.lp.write_text(json.dumps(self.lock))
        self.m={'schema':'EXPLICIT_RUN_REGISTRY_V1','run_id':self.run,'backend':BACKEND,'plan_path':str(self.pp),'plan_sha256':sha(self.pp),'schedule_lock_path':str(self.lp),'schedule_lock_sha256':sha(self.lp),'actions':self.actions,'controller_unit':'controller','observer_unit':'observer'};self.write()
    def write(self):self.mp.write_text(json.dumps(self.m));self.hash=sha(self.mp)
    def tearDown(self):self.tmp.cleanup()
    def bundle(self):return load_bundle(self.mp,self.hash,self.run,self.root)
    def inventory(self,i):
        a=self.actions[i];return {a['unit']+'.timer':{'worker':a['worker'],'active':'active','service_unit':a['unit']+'.service','argv':expected_argv(self.mp,self.hash,a['action_id'],self.root),'planned_epoch':1000+a['offset_seconds'],'accuracy_usec':'10ms','random_delay_usec':'0','persistent':'no'}}
    def check(self,i,present=None,props=None):
        m,p=self.bundle();props=self.inventory(i) if props is None else props
        return verify_timer_inventory(m,p,self.mp,self.hash,self.actions[i]['worker'],set(props) if present is None else present,props,self.root)
    def test_service_pending_legal(self):self.assertTrue(self.check(0)['pass'])
    def test_pod_pending_legal(self):self.assertTrue(self.check(1)['pass'])
    def test_both_workers_pending(self):self.assertTrue(self.check(0)['pass'] and self.check(1)['pass'])
    def test_extra_timer_rejected(self):
        with self.assertRaisesRegex(ValueError,'UNREGISTERED'):self.check(0,{'ops-unregistered.timer'})
    def test_historical_timer_rejected(self):
        with self.assertRaisesRegex(ValueError,'UNREGISTERED'):self.check(0,{'ops-a03-sf-pilot-01-attempt-03-service.timer'})
    def test_run_id_rejected(self):
        with self.assertRaises(ValueError):load_bundle(self.mp,self.hash,'SFDRY-OLD',self.root)
    def test_explicit_lock_path_missing(self):
        self.m['schedule_lock_path']=str(self.root/'wrong-file.json');self.write()
        with self.assertRaises(ValueError):self.bundle()
    def test_manifest_hash_rejected(self):
        with self.assertRaises(ValueError):load_bundle(self.mp,'0'*64,self.run,self.root)
    def test_lock_hash_rejected(self):
        self.m['schedule_lock_sha256']='0'*64;self.write()
        with self.assertRaises(ValueError):self.bundle()
    def test_wrong_worker(self):
        props=self.inventory(0);next(iter(props.values()))['worker']='k8s-worker2'
        with self.assertRaises(ValueError):self.check(0,props=props)
    def test_exec_command_tamper(self):
        props=self.inventory(0);next(iter(props.values()))['argv'][0]='/bin/sh'
        with self.assertRaises(ValueError):self.check(0,props=props)
    def test_clock_schedule_tamper(self):
        props=self.inventory(0);next(iter(props.values()))['planned_epoch']+=1
        with self.assertRaises(ValueError):self.check(0,props=props)
    def test_dry_run_never_selects_fault_backend(self):
        self.m['backend']='REAL_FAULT';self.write()
        with self.assertRaises(ValueError):self.bundle()
    def test_path_escape(self):
        self.m['schedule_lock_path']='/etc/passwd';self.write()
        with self.assertRaises(ValueError):self.bundle()
    def test_cancelled_cannot_rearm(self):
        runtime=self.root/'revoked';runtime.mkdir();(runtime/'REVOKED.json').write_text('{}')
        with patch('fault_worker.bind',return_value=(self.m,self.plan,self.actions[0],runtime)),patch('fault_worker.subprocess.run') as command:
            with self.assertRaisesRegex(RuntimeError,'CANNOT_REARM'):arm(None)
            command.assert_not_called()
    def test_onset_rejects_old_2565ms(self):
        self.assertFalse(onset(self.actions[0],utc(1722.565),{'worker_minus_master_seconds':0,'uncertainty_seconds':.02})['pass'])
    def test_revoked_lease_never_usable(self):
        with self.assertRaisesRegex(RuntimeError,'REVOKED'):validate_lease(self.m,self.hash,self.actions[0],{},1720,True)
    def test_no_marker_permission_can_authorize_formal(self):
        l=self.good_lease();l['permission']='MARKER_ONLY'
        with self.assertRaisesRegex(RuntimeError,'SCOPE'):validate_lease(self.m,self.hash,self.actions[0],l,1720)
    def test_fault_worker_uses_pinned_adapter_only(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/fault_worker.py').read_text()
        self.assertIn('sha(ADAPTER)!=ADAPTER_SHA',source)
        self.assertIn("adapter.IDS[a['action_id']]",source)
        self.assertIn("adapter.cleanup(state)",source)
    def good_lease(self):
        return {'manifest_sha256':self.hash,'run_id':self.run,'action_id':self.actions[0]['action_id'],'permission':'BOUND_QUALIFIED_FAULT',
          'issued_utc':utc(1719),'expires_utc':utc(1723),'safety_pass':True,'authorization_sha256':'a'*64,
          'clock':{'measured_at_master_utc':utc(1719),'worker_minus_master_seconds':0,'uncertainty_seconds':.02}}
    def test_fresh_marker_lease(self):validate_lease(self.m,self.hash,self.actions[0],self.good_lease(),1720)
    def test_expired_lease(self):
        with self.assertRaisesRegex(RuntimeError,'EXPIRED'):validate_lease(self.m,self.hash,self.actions[0],self.good_lease(),1724)
    def test_unsafe_lease(self):
        l=self.good_lease();l['safety_pass']=False
        with self.assertRaisesRegex(RuntimeError,'UNSAFE'):validate_lease(self.m,self.hash,self.actions[0],l,1720)
    def test_lease_cannot_authorize_real_fault(self):
        l=self.good_lease();l['permission']='REAL_FAULT'
        with self.assertRaisesRegex(RuntimeError,'SCOPE'):validate_lease(self.m,self.hash,self.actions[0],l,1720)
if __name__=='__main__':unittest.main()
