#!/usr/bin/env python3
"""Read-only five-host resource/clock/residue evidence for contest admission."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess


HOSTS = {'master': '192.168.53.52', 'w1': '192.168.53.53',
         'w2': '192.168.53.54', 'w3': '192.168.53.55', 'router': '192.168.53.50'}
CODE = r'''
import datetime,json,os,pathlib,shutil,socket,subprocess,time
def cmd(argv):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=20)
 return {'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
memory={l.split(':')[0]:int(l.split()[1])*1024 for l in pathlib.Path('/proc/meminfo').read_text().splitlines() if l.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:'))}
psi={k:pathlib.Path('/proc/pressure/'+k).read_text() for k in ('cpu','memory','io')}
stopped=cmd(['ps','-eo','pid=,stat=,comm='])
stopped['stdout']='\n'.join(l for l in stopped['stdout'].splitlines() if len(l.split())>=3 and 'T' in l.split()[1])
out={'host':socket.gethostname(),'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'cpu_count':os.cpu_count(),'loadavg':os.getloadavg(),'memory':memory,'pressure':psi,
 'root_disk':shutil.disk_usage('/')._asdict(),
 'clock':cmd(['timedatectl','show','-p','NTPSynchronized','-p','NTP','-p','Timezone']),
 'time_sync':cmd(['timedatectl','timesync-status']),
 'qdisc':cmd(['tc','-j','qdisc','show']),
 'egress_filters':cmd(['tc','-j','filter','show','dev','ens160']),
 'stopped_processes':stopped,
 'active_competition_rehearsal_units':cmd(['systemctl','list-units','s5v3*','sf*','ops-*','--state=running','--no-pager','--plain']),
 'active_competition_timers':cmd(['systemctl','list-units','ops-*','--type=timer','--state=active','--no-pager','--plain']),
 'read_only':True}
print(json.dumps(out))
'''


def one(item):
    name, ip = item
    begin = datetime.now(timezone.utc)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-S',
        '/run/ops-a03-ssh/' + name + '.sock', 'root@' + ip,
        shlex.join(['python3', '-c', CODE])], capture_output=True, text=True, timeout=70)
    end = datetime.now(timezone.utc)
    if result.returncode:
        return name, {'status': 'UNKNOWN_READ_FAILED', 'exit_code': result.returncode,
                      'stderr': result.stderr[-500:]}
    row = json.loads(result.stdout)
    row['ssh_request_start_utc'], row['ssh_request_end_utc'] = begin.isoformat(), end.isoformat()
    row['status'] = 'OBSERVED_NOT_INJECTION_AUTHORIZATION'
    return name, row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise RuntimeError('Host audit output already exists')
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = dict(pool.map(one, HOSTS.items()))
    value = {'at_utc': datetime.now(timezone.utc).isoformat(), 'hosts': rows,
             'scope': 'READ_ONLY_COMPETITION_PHASE_A', 'faults_injected': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with args.output.open('x') as f: json.dump(value,f,indent=2); f.write('\n')
    args.output.chmod(0o600)
    print(json.dumps({name:{'host':r.get('host'),'clock':r.get('clock',{}).get('stdout'),
        'memory_available':r.get('memory',{}).get('MemAvailable'),
        'stopped_processes':r.get('stopped_processes',{}).get('stdout')}
        for name,r in rows.items()}))


if __name__ == '__main__': main()
