"""Read-only reconstruction of the two declared A03 timer identities, not a re-admission."""
import json,hashlib,shlex
from pathlib import Path
ROOT=Path('/root/ops-competition-2026')
old=ROOT/'attempt03-20261014';audit=Path('/data/stage5-v3/ops-competition-2026/audits/COMP-SF01-A03-SERVICE-PREFAULT')
read=lambda p:json.loads(p.read_text())
lock=read(old/'plans/schedule-lock-SF-PILOT-01-ATTEMPT-03.json');hosts=read(audit/'hosts.json')['hosts'];rows=[]
for path,expected_hash in lock.items():
    f=Path(path);actual_hash=hashlib.sha256(f.read_bytes()).hexdigest();assert actual_hash==expected_hash
    p=read(f);role='w3' if p['worker']=='k8s-worker3' else 'w2';unit=p['unit']+'.timer'
    candidates=[l for l in hosts[role]['active_competition_timers']['stdout'].splitlines() if l.split() and l.split()[0]==unit]
    assert len(candidates)==1
    argv=shlex.split(candidates[0][candidates[0].index('/usr/bin/python3'):])
    expected=['/usr/bin/python3','-B',str(old/'scripts/local_scheduler.py'),'run','--plan',path,'--sha256',expected_hash]
    assert argv==expected and p['id'].startswith('SF-PILOT-01-ATTEMPT-03-')
    rows.append({'worker':p['worker'],'unit':unit,'plan_file':path,'plan_sha256':actual_hash,'actual_command_matches_explicit_registration':True,'identity_verdict':'REGISTERED_CURRENT_TIMER'})
for role in ('w2','w3'):
    present={l.split()[0] for l in hosts[role]['active_competition_timers']['stdout'].splitlines() if l.split() and l.split()[0].endswith('.timer')}
    expected={r['unit'] for r in rows if r['worker']=='k8s-worker'+role[-1]};assert present==expected
result={'status':'HISTORICAL_IDENTITY_REPLAY_PASS','rows':rows,'original_preflight_result_unchanged':True,
        'scope':'saved Worker/command/plan/hash identity only; historical snapshot did not retain typed microsecond timer properties','actual_timer_execution':False}
out=ROOT/'pre-attempt04-20261014/results/attempt03-readonly-replay.json'
with out.open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
