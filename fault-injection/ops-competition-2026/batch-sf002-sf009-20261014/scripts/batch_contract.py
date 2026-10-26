"""Configuration-driven planning and fail-closed event/case resolution; never injects faults."""
from collections import Counter
from pathlib import PurePosixPath
CASES={
'SF-002':['ServicePause','PodMemoryStress'],
'SF-003':['PodCPUStress','HTTP ReqDelay','PodKill'],
'SF-004':['NetworkDelay','HTTP RespDelay','PodMemoryStress','ServicePause'],
'SF-005':['HTTP RespReplaceCode','ContainerKill','NetworkLoss'],
'SF-006':['PodCPUStress','HTTP ReqAbort','NetworkBandwidth','HTTP RespAbort','ServicePause'],
'SF-007':['NodeCPUStress','HTTP ReqDelay','PodKill','NetworkDelay'],
'SF-008':['NetworkLoss','HTTP RespReplaceCode','PodCPUStress','HTTP RespAbort','NetworkBandwidth','PodMemoryStress'],
'SF-009':['NodeCPUStress','ContainerKill','HTTP RespDelay','HTTP ReqAbort','ServicePause']}
TEMPLATES={2:[12,35],3:[10,26,42],4:[9,21,33,45],5:[8,18,28,38,48],6:[7,15,23,31,39,47]}
G=1024**3
DIMENSIONS={'ServicePause':('Code','ServicePause','SERVICE'),'PodMemoryStress':('Resource','PodMemoryStress','POD'),'PodCPUStress':('Resource','PodCPUStress','POD'),'PodKill':('Resource','PodKill','POD'),'ContainerKill':('Resource','ContainerKill','POD'),'NodeCPUStress':('Resource','NodeCPUStress','NODE'),**{'Network'+t:('Network',t,'NETWORK_PATH') for t in ('Delay','Loss','Bandwidth')},**{'HTTP '+t:('HTTP',t,'SERVICE') for t in ('ReqDelay','RespDelay','ReqAbort','RespAbort','RespReplaceCode')}}

def capacity(free):
    # SAME ATTEMPT04 minimum envelope, not lowered to its observed 11.38GB size.
    demand={'master_root':64*1024**2,'master_data':34*G,'w2_data':31*G,'w3_data':29*G}
    floors={'master_root':46000000000,'master_data':64*G,'w2_data':50000000000,'w3_data':50000000000}
    checks={k:{'available_bytes':free[k],'peak_increment_bytes':demand[k],'remaining_at_peak_bytes':free[k]-demand[k],'floor_bytes':floors[k],'shortfall_bytes':max(0,floors[k]+demand[k]-free[k]),'pass':free[k]-demand[k]>=floors[k]} for k in demand}
    return {'status':'STORAGE_PASS' if all(v['pass'] for v in checks.values()) else 'STORAGE_BLOCKED','checks':checks,'scope':'minimum validated SF001 envelope; extra mechanisms/probes require additional budgeting, never a smaller envelope'}

def validate_case_config(plan):
    ident=plan['case_id'];events=plan['fault_events']
    if ident not in CASES or [e['mechanism'] for e in events]!=CASES[ident]:raise ValueError('REGISTERED_COMBINATION_CHANGED')
    if not 2<=len(events)<=6 or len({e['fault_event_id'] for e in events})!=len(events):raise ValueError('EVENT_COUNT_OR_DUPLICATE_ID')
    for i,e in enumerate(events,1):
        if e['fault_event_id']!=f'{ident}-F{i:02d}':raise ValueError('EVENT_ID_BINDING')
        if (e['fault_category'],e['fault_type'],e['target_layer'])!=DIMENSIONS[e['mechanism']]:raise ValueError('TAXONOMY_BINDING')
        if e['mechanism'] in ('ServicePause','PodMemoryStress') and e['duration_seconds']!=300:raise ValueError('QUALIFIED_DURATION_CHANGED')
        if e['mechanism']=='PodMemoryStress' and e['requested_parameters']!={'allocation_bytes':33554432}:raise ValueError('QUALIFIED_MEMORY_STRENGTH_CHANGED')
    if plan['duration_seconds']!=3600 or plan['maximum_attempts']!=1:raise ValueError('CASE_WINDOW_OR_RETRY')
    return True

def time_plan(events,first_not_before=720,last_recovery_min=180):
    """Validate explicit contracts; absence is UNKNOWN, never silently assign a short duration."""
    reasons=[];previous_ready=0
    for i,e in enumerate(events):
        need=('start_seconds','duration_seconds','recovery_seconds','precheck_seconds','recovery_check_seconds')
        if any(e.get(k) is None for k in need):return {'status':'TIME_PLAN_PENDING_MECHANISM_CONTRACT','reason':'unapproved duration/recovery contract','event':e['fault_event_id']}
        start=e['start_seconds'];end=start+e['duration_seconds']
        if i==0 and start<first_not_before:reasons.append('FIRST_CAPTURE_AUDIT_AND_BASELINE_NOT_READY')
        if i and start-e['precheck_seconds']<previous_ready:reasons.append('PRIOR_RECOVERY_OR_NEXT_PREFLIGHT_OVERLAP')
        previous_ready=end+e['recovery_seconds']+e['recovery_check_seconds']
        if end>3600:reasons.append('FAULT_EXCEEDS_HOUR')
    last=events[-1]
    if previous_ready>3600 or 3600-last['start_seconds']-last['duration_seconds']<last_recovery_min:reasons.append('FINAL_RECOVERY_WINDOW_TOO_SHORT')
    return {'status':'TIME_PLAN_PASS' if not reasons else 'TIME_PLAN_BLOCKED','reasons':sorted(set(reasons)),'recovery_completed_by_seconds':previous_ready}

def capture_offsets(events):
    # Preserve exact windows and do not split a long-lived fault's spans mid-fault.
    boundaries={0,3600,*range(300,3600,300)}
    for e in events:
        if e.get('start_seconds') is None or e.get('duration_seconds') is None:raise ValueError('NO_FROZEN_TIME_CONTRACT')
        left,right=e['start_seconds'],e['start_seconds']+e['duration_seconds']
        boundaries={t for t in boundaries if not left<t<right}
    result=sorted(boundaries)
    if any(b-a>600 for a,b in zip(result,result[1:])):raise ValueError('CAPTURE_CHUNK_EXCEEDS_VALIDATED_MAXIMUM')
    return result

def http_scope(proof):
    if proof.get('application_protocol')!='HTTP' or not proof.get('content_type') or str(proof.get('content_type','')).startswith('application/grpc'):raise ValueError('GRPC_IS_NOT_HTTP_FAULT_SCOPE')
    if not proof.get('public_trace_reference') or not proof.get('verified_route') or not proof.get('control_routes_excluded'):raise ValueError('HTTP_SCOPE_NOT_PROVEN')
    return True

STATES={'REGISTERED':{'PREFLIGHT_PASSED','ABORTED_NOT_EXECUTED'},'PREFLIGHT_PASSED':{'AUTHORIZED','ABORTED_NOT_EXECUTED'},'AUTHORIZED':{'STARTED','ABORTED_NOT_EXECUTED'},'STARTED':{'ACTION_COMPLETED','CLEANUP_REQUIRED'},'ACTION_COMPLETED':{'RECOVERY_CONFIRMED','CLEANUP_REQUIRED'},'CLEANUP_REQUIRED':{'RECOVERY_CONFIRMED','RECOVERY_FAILED'},'RECOVERY_CONFIRMED':set(),'RECOVERY_FAILED':set(),'ABORTED_NOT_EXECUTED':set()}
class EventMachine:
    def __init__(self,ident):self.ident=ident;self.state='REGISTERED';self.history=[]
    def move(self,new,evidence):
        if new not in STATES[self.state] or not evidence:raise ValueError('INVALID_TRANSITION_OR_MISSING_EVIDENCE')
        self.history.append({'from':self.state,'to':new,'evidence':evidence});self.state=new
        return new

def decide_event(v):
    if type(v['executed']) is not bool:raise ValueError('EXECUTION_BOOLEAN_REQUIRED')
    if not v['executed']:return {'status':'NOT_EXECUTED','batch_stop':False}
    if v.get('safety_violation') or v['Recovery']['status']!='PASS':return {'status':'RECOVERY_FAILED' if v['Recovery']['status']=='FAIL' else 'REVIEW_REQUIRED','batch_stop':True}
    for key,item in v.items():
        if isinstance(item,dict) and item.get('status')=='PASS':
            if not item.get('evidence'):raise ValueError('PASS_WITHOUT_EVIDENCE')
            if key=='Observability' and any(PurePosixPath(r.split('#',1)[0]).parts not in [('public',n) for n in ('metrics.parquet','logs.parquet','traces.parquet','topology.json')] for r in item['evidence']):raise ValueError('PRIVATE_EVIDENCE_IS_NOT_PUBLIC_OBSERVABILITY')
    for key,status in [('Injection Success','INJECTION_INEFFECTIVE'),('Target Correctness','REVIEW_REQUIRED'),('Fault Intensity','INJECTION_INEFFECTIVE'),('Telemetry Quality','TELEMETRY_BLOCKED'),('Observability','NOT_OBSERVABLE')]:
        if v[key]['status']=='FAIL':return {'status':status,'batch_stop':key=='Target Correctness'}
    required=['Injection Success','Target Correctness','Fault Intensity','Telemetry Quality','Observability','Recovery']
    if any(v[k]['status']!='PASS' for k in required):return {'status':'REVIEW_REQUIRED','batch_stop':False}
    impact=v['User Impact']
    if impact['status']=='UNKNOWN' or not impact.get('evidence'):return {'status':'REVIEW_REQUIRED','batch_stop':False}
    if impact['status']=='PASS' and impact.get('outcome')=='USER_IMPACT_CONFIRMED':return {'status':'SUPPORTED_OBSERVABLE','batch_stop':False}
    if impact.get('outcome')=='NO_USER_IMPACT_OBSERVED':return {'status':'SUPPORTED_LOW_IMPACT','batch_stop':False}
    return {'status':'REVIEW_REQUIRED','batch_stop':False}

def decide_case(plan,events,quality):
    if not 2<=len(plan['fault_events'])<=6:raise ValueError('CASE_REQUIRES_2_TO_6_EVENTS')
    if len(events)!=len(plan['fault_events']) or {x['fault_event_id'] for x in events}!={x['fault_event_id'] for x in plan['fault_events']}:raise ValueError('CANNOT_DROP_PLANNED_EVENTS')
    required={'full_hour','telemetry','public_schema','public_private_boundary','archive','gt_traceability','final_safety'}
    if set(quality)!=required or any(type(x) is not bool for x in quality.values()):raise ValueError('INCOMPLETE_QUALITY_CONTRACT')
    if any(e['batch_stop'] for e in events):return 'CASE_SAFETY_ABORTED'
    if not all(quality.values()) or not all(e['status'] in ('SUPPORTED_OBSERVABLE','SUPPORTED_LOW_IMPACT') for e in events):return 'CASE_REVIEW_REQUIRED'
    return 'CASE_READY'

def activate_gate(storage,mechanisms,timing,engineering,live_health,scope_authorized):
    blockers=[]
    if storage['status']!='STORAGE_PASS':blockers.append('STORAGE_BLOCKED')
    if not 2<=len(mechanisms)<=6 or any(v!='ENGINEERING_SAFETY_PASS' for v in mechanisms):blockers.append('MECHANISM_OR_SAFETY_BLOCKED')
    if timing['status']!='TIME_PLAN_PASS':blockers.append(timing['status'])
    if engineering!='FULL_PRODUCTION_PIPELINE_VALIDATED':blockers.append('PRODUCTION_PIPELINE_NOT_VALIDATED')
    if not live_health:blockers.append('FRESH_SAFETY_REQUIRED')
    if not scope_authorized:blockers.append('EVENT_SCOPE_NOT_AUTHORIZED')
    return {'allowed':not blockers,'blockers':blockers}
