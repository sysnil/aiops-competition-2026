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
 obj=shlex.split(cmd(['busctl','call','org.freedesktop.systemd1','/org/freedesktop/systemd1','org.freedesktop.systemd1.Manager','GetUnit','s',unit]))[1]
 exact=cmd(['busctl','get-property','org.freedesktop.systemd1',obj,'org.freedesktop.systemd1.Timer','NextElapseUSecRealtime'])
 if not exact.startswith('t '):raise RuntimeError('No typed microsecond timer property')
 next_usec=int(exact.split()[1])
 raw=cmd(['systemctl','show',props['Unit'],'-p','ExecStart','--value'])
 match=re.search(r'argv\[\]=(.*?) ;',raw)
 rows[unit]={'worker':socket.gethostname(),'active':props['ActiveState'],'service_unit':props['Unit'],
 'argv':shlex.split(match.group(1)) if match else [],'exec_start_raw':raw,
 'planned_epoch':next_usec/1000000,'next_elapse_usec':next_usec,'dbus_raw':exact,
 'accuracy_usec':props['AccuracyUSec'],'random_delay_usec':props['RandomizedDelayUSec'],'persistent':props['Persistent'],
 'raw_properties':props}
print(json.dumps(rows))
'''
def enrich(hosts):
    for name in ('w2',):
        hosts[name]['timer_properties']=json.loads(remote(name,['python3','-c',CODE]))
    return hosts
