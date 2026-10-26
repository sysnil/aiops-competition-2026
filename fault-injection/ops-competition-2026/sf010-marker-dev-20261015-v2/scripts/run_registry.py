"""Generic 2–6 event registry for harmless scheduler integration only."""
import hashlib,json
from pathlib import Path
from datetime import datetime
ROOT=Path('/root/ops-competition-2026/sf010-marker-dev-20261015-v2')
BACKEND='SF010_SCHEDULER_MARKER_ONLY_V1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def checked_file(path,digest,root):
 p=Path(path)
 if p.is_symlink() or not p.resolve().is_relative_to(Path(root).resolve()):raise ValueError('REGISTRATION_PATH')
 if not p.is_file() or sha(p)!=digest:raise ValueError('REGISTRATION_FILE_HASH')
 return read(p)
def load_bundle(path,digest,expected_run=None,root=ROOT):
 m=checked_file(path,digest,root);run=m.get('run_id','')
 if run!='SF010-MARKER-DRYRUN-02':raise ValueError('MARKER_RUN_ID_ONLY')
 if expected_run is not None and expected_run!=run:raise ValueError('RUN_ID')
 if m.get('schema')!='EXPLICIT_RUN_REGISTRY_V1' or m.get('backend')!=BACKEND:raise ValueError('MARKER_CAPABILITY_REQUIRED')
 p=checked_file(m['plan_path'],m['plan_sha256'],root);lock=checked_file(m['schedule_lock_path'],m['schedule_lock_sha256'],root)
 if p['id']!=run or p['formal_case'] is not False or p['business_fault_permission'] is not False or p['backend']!=BACKEND:raise ValueError('MARKER_SCOPE_ONLY')
 if len(m['actions'])!=2 or p['actions']!=m['actions'] or lock['actions']!=m['actions'] or lock['run_id']!=run or lock['plan_sha256']!=m['plan_sha256']:raise ValueError('LOCK_PLAN_ACTION_BINDING')
 if p.get('duration_seconds')!=1800 or [a['offset_seconds'] for a in m['actions']]!=[600,960]:raise ValueError('SF010_30MIN_SCHEDULE')
 if (m['controller_unit'],m['observer_unit'])!=(p['controller_unit'],p['observer_unit']):raise ValueError('CONTROL_UNIT_BINDING')
 ids=set();previous=-1
 for a in m['actions']:
  if a['action_id'] in ids or a['run_id']!=run:raise ValueError('DUPLICATE_OR_FOREIGN_EVENT')
  ids.add(a['action_id'])
  if a['worker']!='k8s-worker3' or a['unit']!='ops-sf010-marker-'+a['action_id'].lower():raise ValueError('WORKER_OR_UNIT')
  if a['backend']!=BACKEND or a['execute']!='WRITE_LOCAL_TIME_MARKER' or a['revoke']!='REVOKE_AND_CANCEL_MARKER' or a['targets']:raise ValueError('NO_BUSINESS_CAPABILITY')
  if a['registration_file']!=m['plan_path'] or a['duration_seconds']!=3 or a['onset_tolerance_seconds']!=2:raise ValueError('MARKER_CONTRACT')
  if a['offset_seconds']<=previous or abs(stamp(a['planned_utc'])-stamp(p['start_utc'])-a['offset_seconds'])>.00001:raise ValueError('EVENT_TIMING')
  previous=a['offset_seconds']
  if not Path(a['runtime']).resolve().is_relative_to((Path(root)/'worker-runtime').resolve()):raise ValueError('RUNTIME_PATH')
 return m,p
def action_for(m,action_id,worker=None):
 rows=[a for a in m['actions'] if a['action_id']==action_id]
 if len(rows)!=1 or (worker and rows[0]['worker']!=worker):raise ValueError('ACTION_NOT_ON_WORKER')
 return rows[0]
def expected_argv(path,digest,ident,root=ROOT):return ['/usr/bin/python3','-B',str(Path(root)/'scripts/marker_worker.py'),'fire','--manifest',str(path),'--manifest-sha',digest,'--action-id',ident]
def verify_timer_inventory(m,p,mpath,mhash,worker,present,properties,root=ROOT):
 expected={a['unit']+'.timer':a for a in m['actions'] if a['worker']==worker}
 if set(present)-set(expected):raise ValueError('UNREGISTERED_TIMER')
 for unit in present:
  a=expected[unit];v=properties[unit]
  if v['worker']!=worker or v['active']!='active' or v['service_unit']!=a['unit']+'.service':raise ValueError('ACTUAL_TIMER_BINDING')
  if v['argv']!=expected_argv(mpath,mhash,a['action_id'],root):raise ValueError('ACTUAL_COMMAND')
  if abs(v['planned_epoch']-stamp(a['planned_utc']))>.001:raise ValueError('ACTUAL_TIMER_TIME')
  if (v['accuracy_usec'],v['random_delay_usec'],v['persistent'])!=('10ms','0','no'):raise ValueError('TIMER_SAFETY_CONFIG')
 return {'pass':True,'present':sorted(present),'registered':sorted(expected),'unknown':[]}
