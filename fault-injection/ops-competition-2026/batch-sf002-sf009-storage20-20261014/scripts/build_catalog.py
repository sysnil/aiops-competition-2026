"""Build a reviewable batch catalog from actual inventory; no registration/activation side effects."""
import pathlib,json,hashlib,sys,collections
from datetime import datetime,timezone
import yaml
from batch_contract import *
R=pathlib.Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text())
def write(path,v):
 with path.open('x') as f:
  if path.suffix=='.yaml':yaml.safe_dump(v,f,allow_unicode=True,sort_keys=False)
  else:json.dump(v,f,indent=2,ensure_ascii=False)
def main():
 inventory=read(R/'evidence/source-reference-inventory.json');sources={x['path']:x for x in inventory}
 hosts=read(R/'evidence/hosts-initial.json')['hosts'];pods=read(R/'evidence/kubernetes-pods.json')['items'];crds=read(R/'evidence/crds.json')['items'];cms=read(R/'evidence/kubernetes-configmaps.json')['items']
 def proof(path):return {'path':path,'sha256':sources[path]['sha256']}
 base='/root/ops-competition-2026/attempt04-20261014'
 ref=proof('/root/ops-competition-2026/reports/SF-PILOT-01-attempt04-report.md')
 storage=capacity({'master_root':hosts['master']['root_disk']['free'],'master_data':hosts['master']['data_disk']['free'],'w2_data':hosts['w2']['data_disk']['free'],'w3_data':hosts['w3']['data_disk']['free']})
 storage['basis']=proof(base+'/scripts/storage_budget.py');storage['sampled_at_utc']=read(R/'evidence/hosts-initial.json')['at_utc'];write(R/'results/storage-admission.json',storage)
 topology=[{'service':p['metadata']['labels'].get('opentelemetry.io/name'),'pod':p['metadata']['name'],'uid':p['metadata']['uid'],'node':p['spec']['nodeName'],'ip':p['status'].get('podIP'),'ready':all(c.get('ready') for c in p['status'].get('containerStatuses',[])),'containers':[{'id':c.get('containerID'),'restart_count':c['restartCount']} for c in p['status'].get('containerStatuses',[])]} for p in pods]
 write(R/'results/current-topology.json',{'at_utc':datetime.now(timezone.utc).isoformat(),'entities':topology,'path_membership_inferred_from_placement':False})
 registry=[]
 definitions={
 'ServicePause':('PINNED_COMPETITION_ADAPTER','ENGINEERING_REFERENCE_AVAILABLE',['FRESH_PROCESS_IDENTITY_AND_WATCHDOG_REQUIRED','STORAGE_BLOCKED'],['recommendation'],[proof('/root/ops-competition-2026/scripts/competition_adapter.py'),ref]),
 'PodMemoryStress':('PINNED_COMPETITION_ADAPTER','ENGINEERING_REFERENCE_AVAILABLE',['FRESH_UID_CGROUP_AND_MEMORY_HEADROOM_REQUIRED','STORAGE_BLOCKED'],['frontend'],[proof('/root/ops-competition-2026/scripts/competition_adapter.py'),ref]),
 'PodCPUStress':('LEGACY_IN_POD_ADAPTER','SAFETY_BLOCKED',['LEGACY_AD_WORKER2_TARGET_NO_LONGER_EXISTS','CLEANUP_USES_PROCESS_NAME_NOT_RUN_OWNED_PIDS','MASTER_API_DEPENDENT_WATCHDOG_AND_ONSET_NOT_BATCH_BOUND','CURRENT_BOUNDED_STRESS_CONTRACT_NOT_APPROVED'],['ad'],[proof('/root/stage4f-cross-layer-validation/scripts/pod_cpu_exec.py')]),
 'PodKill':('KUBERNETES_NATIVE_PRIMITIVE_ONLY','SAFETY_BLOCKED',['NO_COMPETITION_UID_PRECONDITION_DELETE_ADAPTER','NO_BOUNDED_REPLICA_REPLACEMENT_WATCHDOG','CURRENT_OBSERVER_REJECTS_UID_CHANGE'],['frontend','recommendation'],[]),
 'ContainerKill':('CONTAINERD_KUBELET_PRIMITIVE_ONLY','SAFETY_BLOCKED',['NO_COMPETITION_CONTAINER_ID_PRECONDITION_ADAPTER','NO_BOUNDED_RESTART_RECOVERY_CONTRACT','CURRENT_OBSERVER_REJECTS_CONTAINER_ID_RESTART_CHANGE'],['frontend','recommendation'],[]),
 'NodeCPUStress':('LEGACY_HOST_CPU_ADAPTER','SAFETY_BLOCKED',['SIX_OF_EIGHT_CORE_LEGACY_INTENSITY_NOT_CURRENT_APPROVAL','ALL_WORKERS_HOST_TELEMETRY_OR_STORAGE_COMPONENTS','DURING_CPU_PSI_STOP_CONTRACT_NOT_APPROVED','RUN_LEASE_AND_FEEDBACK_NOT_INTEGRATED'],['k8s-worker1','k8s-worker2','k8s-worker3'],[proof('/root/stage5-v3-mechanism-qualification/scripts/resource_fault.py'),proof('/root/stage5-v3-final-fast-ready/reports/four-layer-readiness.md')]),
 'NetworkDelay':('LEGACY_TC_NETEM','SAFETY_BLOCKED',['OLD_DIRECTION_TARGET_AND_INTERFACE_BINDING','FRESH_ROUTE_FLOW_AND_CONTROL_PROOFS_REQUIRED','TELEMETRY_AND_MANAGEMENT_FLOW_EXCLUSION_NOT_PROVEN','BATCH_OWNED_QDISC_RECOVERY_NOT_VERIFIED'],[],[proof('/root/stage4f-cross-layer-validation/scripts/host_fault.py')]),
 'NetworkLoss':('LEGACY_TC_NETEM','SAFETY_BLOCKED',['FULL_DESTINATION_POD_PREFIX_OR_NODE_IP_FILTER','COLLECTOR_PATH_INTERFERENCE_NOT_EXCLUDED','FRESH_ROUTE_FLOW_AND_CONTROL_PROOFS_REQUIRED','BATCH_OWNED_QDISC_RECOVERY_NOT_VERIFIED'],[],[proof('/root/cross-layer-rehearsal-phase4/scripts/network_fault.py')]),
 'NetworkBandwidth':('LEGACY_TC_TBF','SAFETY_BLOCKED',['FULL_DESTINATION_POD_PREFIX_OR_NODE_IP_FILTER','COLLECTOR_PATH_INTERFERENCE_NOT_EXCLUDED','FRESH_ROUTE_FLOW_AND_CONTROL_PROOFS_REQUIRED','BATCH_OWNED_QDISC_RECOVERY_NOT_VERIFIED'],[],[proof('/root/cross-layer-fault-rehearsal/scripts/network_fault.py')]),
 'HTTP ReqDelay':('LIVE_ENVOY_HEADER_DELAY_PRIMITIVE','SAFETY_BLOCKED',['HEADER_DELAY_FILTER_PRESENT_NOT_A_RUN_SCOPED_ADAPTER','NO_REGISTERED_ROUTE_CONTROL_EXCLUSION_AND_DELAY_CEILING','NO_BOUNDED_EVENT_RECOVERY_AND_LEASE_INTEGRATION'],['frontend-proxy HTTP routes'],[]),
 **{n:('NO_CONFIGURED_STAGE_SPECIFIC_ADAPTER','MECHANISM_UNAVAILABLE',['NO_CURRENT_'+n.replace(' ','_').upper()+'_ADAPTER','NO_CHAOS_MESH_CRD','REQUEST_DELAY_MUST_NOT_BE_RELABELED_AS_RESPONSE_FAULT'],['frontend-proxy HTTP routes'],[]) for n in ['HTTP RespDelay','HTTP ReqAbort','HTTP RespAbort','HTTP RespReplaceCode']}}
 for name,(impl,status,blockers,targets,refs) in definitions.items():
  cat,typ,layer=DIMENSIONS[name];qualified=name in ('ServicePause','PodMemoryStress')
  registry.append({'mechanism':name,'fault_category':cat,'fault_type':typ,'target_layer':layer,'implementation':impl,'engineering_status':status,'blockers':blockers,
    'candidate_targets_not_activated':targets,'actual_batch_activations':0,'batch_empirical_status':'NOT_EXECUTED',
    'historical_contest_evidence':{'case_id':'SF-001','events':1,'conclusion':'SUPPORTED_OBSERVABLE' if name=='ServicePause' else 'SUPPORTED_LOW_IMPACT','source':ref} if qualified else None,
    'reference_sources':refs,'maximum_confirmed_competition_duration_seconds':300 if qualified else None,
    'independent_full_fault_rehearsal_required':False,'production_activation_allowed':False,
    'public_direct_evidence_requirements':{'SERVICE':'scope-correct public errors/timeouts/call outcomes plus service/replica evidence','POD':'UID-linked resources and actual instance/control evidence','NODE':'node resources, protected-component safety and multiple colocated active Pod observations','NETWORK_PATH':'fresh path/flow proof, independent network measurement and unaffected control flows'}[layer]})
 registry_map={v['mechanism']:v for v in registry}
 write(R/'mechanism-registry.yaml',{'status':'AUDITED_NOT_EXECUTION_PERMISSION','mechanisms':registry,'chaos_mesh_present':any('chaos-mesh' in c['metadata']['name'] for c in crds),'http_header_delay_filter_present':any('envoy.filters.http.fault' in json.dumps(c.get('data',{})) for c in cms),'thresholds_not_relaxed':True})
 write(R/'fault-taxonomy.yaml',{'status':'DRAFT_FOR_THIS_BATCH','dimensions':['fault_category','fault_type','target_layer'],'service_pause_note':'ServicePause is Code/process execution category and SERVICE target layer; original Service grouping retained as display concept.','types':[{'mechanism':n,'fault_category':v[0],'fault_type':v[1],'target_layer':v[2]} for n,v in DIMENSIONS.items()],'extensions':{'JVM':'NOT_SCHEDULED_REQUIRES_RUNTIME_SCOPE_RECOVERY_PROOF','DNS':'NOT_SCHEDULED_CONTROL_PLANE_ISOLATION_REQUIRED','Time':'NOT_SCHEDULED_CLOCK_AND_CONTROL_ISOLATION_REQUIRED'}})
 plans=[];results=[];validation=[]
 for ident,names in CASES.items():
  events=[]
  for i,name in enumerate(names,1):
   cat,typ,layer=DIMENSIONS[name];known=name in ('ServicePause','PodMemoryStress')
   events.append({'fault_event_id':f'{ident}-F{i:02d}','mechanism':name,'fault_category':cat,'fault_type':typ,'target_layer':layer,
    'target_entity':None,'target_uid':None,'actual_target':None,'worker':None,'duration_seconds':300 if known else None,
    'requested_parameters':{'allocation_bytes':33554432} if name=='PodMemoryStress' else {'scope':'both recommendation replicas'} if name=='ServicePause' else None,
    'actual_parameters':None,'start_seconds':TEMPLATES[len(names)][i-1]*60,'recovery_seconds':240 if name=='ServicePause' else 180 if name=='PodMemoryStress' else None,'recovery_check_seconds':45,'precheck_seconds':105,
    'actual_start':None,'actual_end':None,'injection_status':'NOT_EXECUTED','watchdog_status':'NOT_ARMED','recovery_status':'NOT_APPLICABLE_NO_INJECTION'})
  plan={'case_id':ident,'status':'CANDIDATE_NOT_REGISTERED','duration_seconds':3600,'maximum_attempts':1,'seed':20261014000+int(ident[-3:]),'fault_events':events,'original_candidate_start_minutes':TEMPLATES[len(names)],'workload':{'profile':'MEDIUM','sessions_per_second':1.4,'image_per_second':1,'cart_per_second':2},'T0_utc':None,'source_scope':'directional authorization; no automatic replacement; no real activation while blocked'}
  validate_case_config(plan);timing=time_plan(events)
  blockers=['STORAGE_BLOCKED']+sorted(set(registry_map[n]['engineering_status'] for n in names if registry_map[n]['engineering_status']!='ENGINEERING_REFERENCE_AVAILABLE'))
  if timing['status']!='TIME_PLAN_PASS':blockers.append(timing['status'])
  plan['time_plan_audit']=timing;plan['capture_offsets_seconds']=capture_offsets(events) if timing['status']=='TIME_PLAN_PASS' else None
  write(R/'plans'/(ident+'-candidate.json'),plan);plans.append(plan)
  results.append({'case_id':ident,'planned_faults':len(names),'actual_faults':0,'safety_recovery':'NOT_APPLICABLE_NO_INJECTION','telemetry_quality':'NOT_COLLECTED','public':'NOT_CREATED','gt':'NOT_CREATED','status':'STORAGE_BLOCKED','additional_blockers':blockers,'preregistered':False,'production_started':False})
  validation.extend({'case_id':ident,**e,'mechanism_validation_status':'NOT_EXECUTED','dimensions':{k:{'status':'UNKNOWN','evidence':[],'reason':'no production was started'} for k in ['Injection Success','Target Correctness','Fault Intensity','Observability','Telemetry Quality','User Impact','Recovery','Interference','Evidence Quality']}} for e in events)
 write(R/'case-catalog.yaml',{'status':'CANDIDATE_PLANS_ONLY','planned_cases':8,'planned_faults':32,'actual_cases_started':0,'cases':[{'case_id':p['case_id'],'fault_types':CASES[p['case_id']],'candidate_plan':'plans/'+p['case_id']+'-candidate.json','status':'STORAGE_BLOCKED'} for p in plans]})
 count=Counter(n for row in CASES.values() for n in row)
 coverage={'planned_mechanism_counts':dict(count),'actual_mechanism_counts':{n:0 for n in DIMENSIONS},'planned_category_counts':dict(Counter(DIMENSIONS[n][0] for row in CASES.values() for n in row)),'planned_layer_counts':dict(Counter(DIMENSIONS[n][2] for row in CASES.values() for n in row)),'actual_faults':0,'historical_SF001_excluded_from_batch_counts':True}
 write(R/'coverage-matrix.json',coverage);write(R/'results/case-results.json',results);write(R/'results/fault-validation-not-executed.json',validation)
 write(R/'results/fault-capability-matrix.json',[{'fault_type':n,'planned_cases':[c for c,v in CASES.items() if n in v],'actual_executions':0,'injection_success':'UNKNOWN','public_observable':'UNKNOWN','user_impact':'UNKNOWN','recovery':'NOT_APPLICABLE','batch_conclusion':'NOT_EXECUTED','engineering_status':registry_map[n]['engineering_status'],'historical_reference':registry_map[n]['historical_contest_evidence'],'blockers':registry_map[n]['blockers']} for n in DIMENSIONS])
 # Explicit provisional envelope, never mistaken for a registered duration.
 envelope=[]
 for count_ in range(2,7):
  ev=[{'fault_event_id':f'ENVELOPE-{i+1}','start_seconds':720+i*720,'duration_seconds':300,'recovery_seconds':240,'recovery_check_seconds':45,'precheck_seconds':105} for i in range(count_)]
  envelope.append({'event_count':count_,'provisional_all_300s_starts':[e['start_seconds'] for e in ev],'not_approved_not_a_plan':True,'audit':time_plan(ev)})
 write(R/'results/provisional-time-envelope.json',envelope)
 print(json.dumps({'catalog':'BUILT','cases':8,'events':32,'mechanisms':14,'storage':storage['status'],'formal_cases_started':0,'real_faults_injected':0}))
if __name__=='__main__':main()
