import copy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import competition_adapter as a

class AdapterTests(unittest.TestCase):
    def reg(self):
        return {'id':'COMP-SF01-SERVICE-SHORT','layer':'SERVICE','duration_seconds':30,
            'contract_sha256':'hash','adapter_sha256':'hash','host':'k8s-worker3',
            'targets':[{'uid':'a','service':'recommendation'}, {'uid':'b','service':'recommendation'}]}
    def health(self):
        return {'status':'COMPETITION_PREFLIGHT_AUTHORIZED','faults_allowed':True,
            'registration_sha256':'reg','at_utc':datetime.now(timezone.utc).isoformat(),
            'actual_environment_pass':True,'independent_watchdog_pass':True}
    def check(self,reg=None,health=None):
        with patch.object(a,'sha',return_value='hash'),patch.object(a.socket,'gethostname',return_value='k8s-worker3'),patch.object(a,'verify_target'),patch.object(a,'mem_available',return_value=20*1024**3):
            a.validate_registration(reg or self.reg(),health or self.health(),'reg')
    def test_scope_and_duration(self):
        self.check()
        for key,value in [('duration_seconds',480),('id','SF-PILOT-02'),('layer','NODE'),('host','k8s-master'),('contract_sha256','changed')]:
            r=self.reg();r[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.check(r)
    def test_no_research_or_false_authorization_substitute(self):
        for key,value in [('faults_allowed',False),('status','GLOBAL_SAFETY_PASS'),('independent_watchdog_pass',False),('actual_environment_pass',False),('registration_sha256','changed')]:
            h=self.health();h[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.check(health=h)
    def test_stale_health(self):
        h=self.health();h['at_utc']=(datetime.now(timezone.utc)-timedelta(seconds=181)).isoformat()
        with self.assertRaises(RuntimeError):self.check(health=h)
    def test_recycled_pid_never_matches(self):
        item={'pid':10,'start_ticks':'old','cgroup':'/x'}
        with patch.object(a,'proc',return_value={'pid':10,'start_ticks':'new','cgroup':'/x'}):
            self.assertFalse(a.same(item));self.assertFalse(a.same_child(item))
    def test_child_move_write_crash_window(self):
        item={'pid':10,'start_ticks':'1','cgroup':'/launcher','allowed_cgroups':['/launcher','/target']}
        for cg in ['/launcher','/target']:
            with patch.object(a,'proc',return_value={'pid':10,'start_ticks':'1','cgroup':cg}):self.assertTrue(a.same_child(item))
        with patch.object(a,'proc',return_value={'pid':10,'start_ticks':'1','cgroup':'/unrelated'}):self.assertFalse(a.same_child(item))
    def test_cleanup_unknown_pid_refuses_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'state.json'
            a.save(p,{'host':'k8s-worker3','layer':'SERVICE','status':'ACTIVE',
                'stopped_targets':[{'pid':10,'start_ticks':'old','cgroup':'/target'}]})
            with patch.object(a,'RUNTIME',root),patch.object(a.socket,'gethostname',return_value='k8s-worker3'),patch.object(a,'same',return_value=False),patch.object(a.os,'kill') as kill:
                with self.assertRaises(RuntimeError):a.cleanup(p)
                kill.assert_not_called()
    def test_state_path_escape(self):
        with self.assertRaises(RuntimeError):a.safe_state(Path('/root/old-research/state.json'))
    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'x.json';a.save(p,{'first':True})
            with self.assertRaises(FileExistsError):a.save(p,{'replaced':True})
    def test_timer_accuracy_is_explicit_and_verified(self):
        with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='active\n')) as run,patch.object(a.subprocess,'check_output',return_value='100ms\n'):
            a.arm(Path('/tmp/test.json'),3,'unit')
            self.assertIn('--timer-property=AccuracySec=100ms',run.call_args_list[0].args[0])
        with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='active\n')),patch.object(a.subprocess,'check_output',return_value='1min\n'):
            with self.assertRaises(RuntimeError):a.arm(Path('/tmp/test.json'),3,'unit')
    def test_expired_transient_timer_is_verified_not_silently_ignored(self):
        with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=5,stderr='not loaded')),patch.object(a.subprocess,'check_output',return_value='LoadState=not-found\nActiveState=inactive\n'):
            a.stop_timer('unit')
        with patch.object(a.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='permission denied')),patch.object(a.subprocess,'check_output',return_value='LoadState=loaded\nActiveState=active\n'):
            with self.assertRaises(RuntimeError):a.stop_timer('unit')

if __name__=='__main__':unittest.main()
