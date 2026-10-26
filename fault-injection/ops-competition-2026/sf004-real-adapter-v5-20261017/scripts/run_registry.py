"""Exact SF-004 bounded ordinary-business HTTP delay registration."""
import hashlib,json
from pathlib import Path
from datetime import datetime
ROOT=Path('/root/ops-competition-2026/sf004-real-adapter-v5-20261017')
BACKEND='SF004_BOUNDED_HTTP_HOME_DELAY_V1'
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
    if run!='SF-004-ATTEMPT-04':raise ValueError('SF004_ONLY')
    if expected_run is not None and run!=expected_run:raise ValueError('RUN_ID')
    if m.get('schema')!='EXPLICIT_RUN_REGISTRY_V1' or m.get('backend')!=BACKEND:raise ValueError('FORMAL_FAULT_CAPABILITY_REQUIRED')
    p=checked_file(m['plan_path'],m['plan_sha256'],root)
    lock=checked_file(m['schedule_lock_path'],m['schedule_lock_sha256'],root)
    if p['id']!=run or p.get('formal_case') is not True or p.get('backend')!=BACKEND:raise ValueError('PLAN_ID_OR_SCOPE')
    if lock['run_id']!=run or lock['plan_sha256']!=m['plan_sha256']:raise ValueError('LOCK_ID_OR_PLAN_HASH')
    if m['actions']!=[] or lock['actions']!=[] or p['actions']!=[]:raise ValueError('WORKER_FAULT_NOT_AUTHORIZED')
    if (m['controller_unit'],m['observer_unit'])!=(p['controller_unit'],p['observer_unit']):raise ValueError('CONTROL_UNITS_DIVERGE')
    if p['duration_seconds']!=1800 or p.get('authorization_scope')!='ONE_SF004_HTTP_HOME_DELAY60_ONLY':raise ValueError('FORMAL_SCOPE')
    candidates=p.get('targets_and_siblings')
    if (not isinstance(candidates,list) or len(candidates)!=4 or
        len({row.get('uid') for row in candidates})!=4 or
        {name:sum(row.get('service')==name for row in candidates)
         for name in ('frontend','recommendation')}!={'frontend':2,'recommendation':2}):
        raise ValueError('PUBLIC_CANDIDATE_UNIVERSE')
    case_root=Path('/data/stage5-v3/ops-competition-2026/runs/SF-004-ATTEMPT-04')
    if p.get('raw_root')!=str(case_root) or p.get('control_root')!=str(case_root/'control'):raise ValueError('FORMAL_DATA_BOUNDARY')
    http=p.get('request_delay')
    if (not isinstance(http,dict) or http.get('operation')!='home' or
        http.get('offset_seconds')!=900 or http.get('duration_seconds')!=60 or
        http.get('delay_ms')!=250 or http.get('lease_path')!=str(Path(p['control_root'])/'http-reqdelay-lease.json')):raise ValueError('HTTP_FAULT_CONTRACT')
    if p.get('no_worker_timer') is not True or m.get('no_worker_timer') is not True:
        raise ValueError('NO_WORKER_TIMER_SCOPE_REQUIRED')
    return m,p
