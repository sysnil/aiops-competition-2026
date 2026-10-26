"""Read actual active ops timers, command lines and UTC deadlines on each Worker."""
import json,shlex
from master_transport import remote
CODE=r'''
import json,subprocess,socket,re,shlex
def cmd(a):return subprocess.check_output(a,text=True,timeout=15).strip()
text=cmd(['systemctl','list-units','ops-*','--type=timer','--state=active','--plain','--no-legend'])
rows={}
for line in text.splitlines():
 unit=line.split()[0]
 props={}
 for key in ('ActiveState','Unit','AccuracyUSec','RandomizedDelayUSec','Persistent','NextElapseUSecRealtime'):
  props[key]=cmd(['systemctl','show',unit,'-p',key,'--value'])
 raw=cmd(['systemctl','show',props['Unit'],'-p','ExecStart','--value'])
 match=re.search(r'argv\[\]=(.*?) ;',raw)
 rows[unit]={'worker':socket.gethostname(),'active':props['ActiveState'],'service_unit':props['Unit'],
 'argv':shlex.split(match.group(1)) if match else [],'exec_start_raw':raw,
 'planned_epoch':float(cmd(['date','--date',props['NextElapseUSecRealtime'],'+%s.%6N'])),
 'accuracy_usec':props['AccuracyUSec'],'random_delay_usec':props['RandomizedDelayUSec'],'persistent':props['Persistent'],
 'raw_properties':props}
print(json.dumps(rows))
'''
def enrich(hosts):
    for name in ('w2','w3'):
        hosts[name]['timer_properties']=json.loads(remote(name,['python3','-c',CODE]))
    return hosts
