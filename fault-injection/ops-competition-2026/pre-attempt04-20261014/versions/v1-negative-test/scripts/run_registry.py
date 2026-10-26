"""Single explicit run registry. Pure validation; no command execution."""
import hashlib,json,re,shlex
from pathlib import Path
from datetime import datetime
ROOT=Path('/root/ops-competition-2026/pre-attempt04-20261014')
BACKEND='HARMLESS_LOCAL_TIME_MARKER_V1'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def checked_file(path,digest,root):
    p=Path(path)
    if p.is_symlink() or not p.resolve().is_relative_to(Path(root).resolve()):raise ValueError('REGISTRATION_PATH')
    if not p.is_file() or sha(p)!=digest:raise ValueError('REGISTRATION_FILE_HASH')
    return read(p)
def load_bundle(path,digest,expected_run=None,root=ROOT):
    m=checked_file(path,digest,root)
    run=m.get('run_id')
    if not isinstance(run,str) or not re.fullmatch(r'SFDRY-[A-Z0-9-]+',run):raise ValueError('DRY_RUN_NAMESPACE_ONLY')
    if expected_run is not None and run!=expected_run:raise ValueError('RUN_ID')
    if m.get('schema')!='EXPLICIT_RUN_REGISTRY_V1' or m.get('backend')!=BACKEND:raise ValueError('MARKER_BACKEND_ONLY')
    p=checked_file(m['plan_path'],m['plan_sha256'],root)
    lock=checked_file(m['schedule_lock_path'],m['schedule_lock_sha256'],root)
    if p['id']!=run or p.get('formal_case') is not False or p.get('backend')!=BACKEND:raise ValueError('PLAN_ID_OR_SCOPE')
    if lock['run_id']!=run or lock['plan_sha256']!=m['plan_sha256']:raise ValueError('LOCK_ID_OR_PLAN_HASH')
    if len(m['actions'])!=2 or lock['actions']!=m['actions'] or p['actions']!=m['actions']:raise ValueError('ACTION_REGISTRY_DIVERGENCE')
    if (m['controller_unit'],m['observer_unit'])!=(p['controller_unit'],p['observer_unit']):raise ValueError('CONTROL_UNITS_DIVERGE')
    seen=set()
    for a in m['actions']:
        layer=a['mechanism_id'];host={'SERVICE':'k8s-worker3','POD':'k8s-worker2'}.get(layer)
        if layer in seen or not host or a['worker']!=host or a['run_id']!=run:raise ValueError('ACTION_WORKER_BINDING')
        seen.add(layer)
        if a.get('registration_file')!=m['plan_path']:raise ValueError('ACTION_REGISTRATION_REFERENCE')
        if a['execute']!='WRITE_MARKER_AND_MONITOR_LEASE' or a['revoke']!='REVOKE_AND_STOP_OWN_TIMER' or a['backend']!=BACKEND:raise ValueError('UNSAFE_ACTION')
        if a['action_id']!=run+'-'+layer or a['unit']!='ops-p04-'+a['action_id'].lower():raise ValueError('ACTION_ID_OR_UNIT')
        if a['duration_seconds']!=10 or a['onset_tolerance_seconds']!=2:raise ValueError('MARKER_BOUNDS')
        if not a.get('targets') or any(not t.get('uid') or t['node']!=host for t in a['targets']):raise ValueError('TARGET_IDENTITY')
        if not stamp(p['start_utc'])<stamp(a['planned_utc'])<stamp(p['start_utc'])+p['duration_seconds']:raise ValueError('ACTION_WINDOW')
        if not Path(a['runtime']).resolve().is_relative_to((Path(root)/'worker-runtime').resolve()):raise ValueError('RUNTIME_PATH')
    return m,p
def action_for(m,action_id,worker=None):
    matches=[a for a in m['actions'] if a['action_id']==action_id]
    if len(matches)!=1 or (worker and matches[0]['worker']!=worker):raise ValueError('ACTION_NOT_ON_WORKER')
    return matches[0]
def expected_argv(mpath,mhash,action_id,root=ROOT):
    return ['/usr/bin/python3','-B',str(Path(root)/'scripts/marker_worker.py'),'fire','--manifest',str(mpath),'--manifest-sha',mhash,'--action-id',action_id]
def verify_timer_inventory(m,p,mpath,mhash,worker,present,properties,root=ROOT):
    expected={a['unit']+'.timer':a for a in m['actions'] if a['worker']==worker}
    unknown=set(present)-set(expected)
    if unknown:raise ValueError('UNREGISTERED_TIMER:'+','.join(sorted(unknown)))
    for unit in present:
        a=expected[unit];prop=properties[unit]
        if prop['worker']!=worker or prop['active']!='active' or prop['service_unit']!=a['unit']+'.service':raise ValueError('ACTUAL_TIMER_BINDING')
        if prop['argv']!=expected_argv(mpath,mhash,a['action_id'],root):raise ValueError('ACTUAL_EXEC_COMMAND')
        if abs(prop['planned_epoch']-stamp(a['planned_utc']))>.001:raise ValueError('ACTUAL_TIMER_TIME')
        if prop['accuracy_usec']!='10ms' or prop['random_delay_usec']!='0' or prop['persistent']!='no':raise ValueError('TIMER_PROTECTION_CONFIG')
    return {'pass':True,'present':sorted(present),'registered':sorted(expected),'unknown':[]}
