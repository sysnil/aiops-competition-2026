"""Record final observed state without issuing an execution authorization."""
import pathlib,json,sys,concurrent.futures
from datetime import datetime,timezone
from collect_inventory import host,HOSTS,remote
from batch_contract import capacity
R=pathlib.Path(__file__).resolve().parents[1]
def main():
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as p:hosts=dict(p.map(host,HOSTS))
 k=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf']
 nodes=json.loads(remote('master',k+['get','nodes','-o','json']))
 pods=json.loads(remote('master',k+['-n','otel-demo','get','pods','-o','json']))
 code='''import sys,json
sys.path.insert(0,'/root/ops-competition-2026/attempt04-20261014/scripts')
from pilot_common import telemetry_state
x=telemetry_state('ac4b46f1-428e-4814-90ba-c52eaa43e9f3');print(json.dumps({k:x[k] for k in ('at_utc','uid','config_sha256','refused','working_set_bytes','freshness')}))'''
 try:telemetry={'status':'TELEMETRY_OBSERVED_PASS','details':json.loads(remote('master',['python3','-B','-c',code]))}
 except Exception as ex:telemetry={'status':'TELEMETRY_READ_FAILED','error':str(ex)}
 budget=capacity({'master_root':hosts['master']['root_disk']['free'],'master_data':hosts['master']['data_disk']['free'],'w2_data':hosts['w2']['data_disk']['free'],'w3_data':hosts['w3']['data_disk']['free']})
 activity={}
 for h,x in hosts.items():
  stopped=[l for l in x['processes']['stdout'].splitlines()[1:] if len(l.split())>=3 and 'T' in l.split()[1]]
  timers=[l for l in x['timers']['stdout'].splitlines() if any(s in l for s in ('ops-','stage5','rehearsal','chaos'))]
  services=[l for l in x['services']['stdout'].splitlines() if any(s in l for s in ('ops-','stage5','rehearsal','chaos'))]
  q=json.loads(x['qdisc']['stdout']);bad=[v for v in q if v.get('kind') in ('netem','tbf','prio','htb')]
  activity[h]={'stopped_processes':stopped,'experiment_timers':timers,'experiment_units':services,'fault_qdiscs':bad,'residue_check_pass':not(stopped or timers or services or bad)}
 value={'status':'BATCH_SAFETY_BLOCKED','at_utc':datetime.now(timezone.utc).isoformat(),'storage':budget,'hosts':hosts,'activity':activity,
 'nodes_ready':sum(any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions']) for n in nodes['items']),'node_count':len(nodes['items']),
 'pod_ready_count':sum(all(c.get('ready') for c in p['status'].get('containerStatuses',[])) for p in pods['items']),'pod_count':len(pods['items']),
 'telemetry':telemetry,'real_faults_injected_this_batch':0,'formal_cases_started':0,'marker_timers_created':0,'does_not_override_failed_safety_gate':True}
 with (R/'results/final-readonly-audit.json').open('x') as f:json.dump(value,f,indent=2)
 print(json.dumps({'status':value['status'],'nodes_ready':value['nodes_ready'],'pods_ready':value['pod_ready_count'],'telemetry':telemetry['status'],'residue_pass':all(v['residue_check_pass'] for v in activity.values()),'storage':budget['checks']}))
if __name__=='__main__':main()
