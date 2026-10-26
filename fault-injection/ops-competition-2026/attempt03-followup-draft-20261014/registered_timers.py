"""Review-only explicit schedule-lock reader; no systemd or injection capability."""
import hashlib,json
from pathlib import Path

def registered_timers(lock_path,plans_root,run_id,worker):
    root=Path(plans_root).resolve();lock_path=Path(lock_path)
    if lock_path.is_symlink() or lock_path.resolve().parent!=root:raise ValueError('LOCK_PATH')
    mapping=json.loads(lock_path.read_text())
    if not isinstance(mapping,dict) or len(mapping)!=2:raise ValueError('EXACTLY_TWO_REGISTERED_ACTIONS_REQUIRED')
    expected=set();seen=set()
    for name,digest in mapping.items():
        p=Path(name)
        if p.is_symlink() or p.resolve().parent!=root:raise ValueError('PLAN_PATH')
        raw=p.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('PLAN_HASH')
        plan=json.loads(raw)
        if plan.get('id') not in (run_id+'-SERVICE',run_id+'-POD') or plan.get('mode')!='FAULT':raise ValueError('RUN_ID_OR_MODE')
        layer=plan['id'].rsplit('-',1)[1];host='k8s-worker3' if layer=='SERVICE' else 'k8s-worker2'
        if plan.get('worker')!=host or layer in seen:raise ValueError('SCOPE')
        unit=plan.get('unit','')
        if not unit.startswith('ops-') or not unit.replace('-','').isalnum():raise ValueError('UNIT')
        seen.add(layer)
        if host==worker:expected.add(unit+'.timer')
    return expected

def verify_present(present,lock_path,plans_root,run_id,worker):
    expected=registered_timers(lock_path,plans_root,run_id,worker)
    return {'pass':set(present)<=expected,'registered':sorted(expected),'unexpected':sorted(set(present)-expected)}
