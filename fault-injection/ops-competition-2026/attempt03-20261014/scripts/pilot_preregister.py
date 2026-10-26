"""One current production admission and one immutable hour, preserving historical BLOCKED."""
import argparse,subprocess
from pilot_common import *
from competition_observation import storage
def main():
    p=argparse.ArgumentParser();p.add_argument('--inspection',required=True);p.add_argument('--health',required=True);a=p.parse_args()
    h=read(a.health)
    if h['status']!='COMPETITION_ENVIRONMENT_PASS_WATCHDOG_PENDING' or h['current_environment_blockers'] or not 0<=time.time()-stamp(h['at_utc'])<=180:raise RuntimeError('Fresh actual safety required')
    t=read(TELEMETRY)
    if t['status']!='COLLECTOR_PATH_READY_FOR_PILOT' or t['candidate_config_sha256']!=EXPECTED_CONFIG:raise RuntimeError('No verified telemetry prerequisite')
    for role in ('VAL1','VAL2'):
        v=read(DATA/'collector-telemetry-repair-20261013/runs-v3'/role/'assessment.json')
        if v['status']!='NO_FAULT_VALIDATION_PASS':raise RuntimeError('Historical independent validation failed')
    if subprocess.run(['systemctl','is-active','ops-collector-telemetry-repair-rollback.timer'],capture_output=True).returncode==0:raise RuntimeError('Pending Collector rollback')
    state=telemetry_state();storage()
    if read(PROD/'results/scheduler-rehearsal.json')['status']!='NO_FAULT_SCHEDULER_PASS':raise RuntimeError('Local Worker scheduler not verified')
    smoke=DATA/'attempt03-20261014/readiness-smoke/result.json'
    proof=read(smoke)
    if proof['status']!='PRODUCTION_SOFTWARE_AND_LIVE_INPUT_SMOKE_PASS' or proof['requests']!=proof['proxy_matched']:raise RuntimeError('Current live-input smoke not resolved')
    if read(PROD/'results/schema-smoke.json')['status']!='REAL_OBSERVATION_PARQUET_SCHEMA_SMOKE_PASS':raise RuntimeError('Exchange schema not verified')
    budget=read(PROD/'results/archive-budget.json')
    if budget['status']!='DUAL_ARCHIVE_STORAGE_BUDGET_PASS' or any(v['free_bytes']-budget['planned_bytes_per_worker']<budget['minimum_remaining_bytes'] for v in budget['workers'].values()):raise RuntimeError('Insufficient independent Worker archive budget')
    if state['refused']!={'grpc':0.0,'http':0.0}:raise RuntimeError('Collector refuses since verified rollout')
    previous=read(OPS/'plans/competition-source-lock-v1.json')
    if any(sha(p)!=v for p,v in previous['source_sha256'].items()):raise RuntimeError('Qualified adapter/workload/contract changed')
    sources=dict(previous['source_sha256'])
    for path in (PROD/'scripts').glob('*.py'):sources[str(path)]=sha(path)
    additional=[Path('/root/stage5-v3-autonomous-preparation/scripts/schedule_trace_completion.py'),
        Path('/root/stage5-v3-autonomous-preparation/scripts/capture_live.py')]
    sources.update({str(p):sha(p) for p in additional})
    source=read(a.inspection)['targets'];targets=[]
    for row in source:
        if len(row['processes'])!=1 or row['container_task_pid']!=row['processes'][0]['pid'] or not row['conservative_32MiB_plus_32MiB_overhead_fits_80pct']:raise RuntimeError('Target identity/reserve changed')
        targets.append({k:row[k] for k in ('service','name','uid','node','container_id','restart_count')})
    proofs=[]
    for layer in ('SERVICE','POD'):
        ident='COMP-SF01-'+layer+'-300';file=OPS/'qualification'/ident/'decision.json'
        old=read(OPS/'plans'/(ident+'.json'));decision=read(file)
        if not decision['qualified']:raise RuntimeError('Mechanism prerequisite failed')
        for target in old['targets']:
            matches=[r for r in source if r['uid']==target['uid'] and r['container_id']==target['container_id'] and r['node']==target['node']]
            if len(matches)!=1 or any(str(matches[0]['processes'][0][key])!=str(target[key]) for key in ('pid','start_ticks')):raise RuntimeError('Qualification process identity no longer applies')
        proofs.append({'id':ident,'file':str(file),'sha256':sha(file)})
    oldblock=OPS/'qualification/production-telemetry-readiness.json'
    admission={'status':'CURRENT_PRODUCTION_ADMISSION_READY','at_utc':now(),'collector':state,
        'historic_readiness_sha256':sha(TELEMETRY),'health_sha256':sha(a.health),'old_blocked_record_sha256':sha(oldblock),
        'current_smoke_resolution_sha256':sha(smoke),
        'archive_budget_sha256':sha(PROD/'results/archive-budget.json'),'schema_smoke_sha256':sha(PROD/'results/schema-smoke.json'),
        'old_blocked_record_overwritten':False,'approved_scope':'one SF-PILOT-01-ATTEMPT-03 hour only; no contest server access'}
    if RUN.exists() or (OPS/'plans/SF-PILOT-01-ATTEMPT-03.json').exists():raise RuntimeError('No second formal attempt')
    save(PROD/'results/current-admission.json',admission)
    seed=1005100;workload={'duration_seconds':3600,'profile':'MEDIUM','rate_sessions_per_second':1.4,
        'control_streams':[{'name':'image_asset','rate_per_second':1.0,'seed_offset':101},{'name':'cart_view','rate_per_second':2.0,'seed_offset':103}],
        'control_timeout_seconds':8,'control_concurrency_per_stream':{'image_asset':2,'cart_view':4}}
    common={'at_utc':now(),'source_sha256':sources,'prerequisites':proofs,'targets_and_siblings':targets,
        'contract_sha256':sha(OPS/'plans/competition-fault-admission.yaml'),'adapter_sha256':sha(OPS/'scripts/competition_adapter.py'),
        'holder_sha256':sha(OPS/'scripts/resource_memory_holder.py'),'allocation_bytes':33554432,'seed':seed,'workload':workload,
        'current_admission_sha256':sha(PROD/'results/current-admission.json'),'production_telemetry_proof_sha256':sha(TELEMETRY),
        'collector_uid':state['uid'],'collector_config_sha256':EXPECTED_CONFIG,'formal_case':True}
    faults=[]
    for layer,offset in [('SERVICE',720),('POD',2100)]:
        old=read(OPS/'plans'/('COMP-SF01-'+layer+'-300.json'));ident='SF-PILOT-01-ATTEMPT-03-'+layer
        path=OPS/'plans'/(ident+'.json');item={**common,'id':ident,'layer':layer,'host':old['host'],
            'targets':old['targets'],'duration_seconds':300,'offset_seconds':offset}
        save(path,item);faults.append({'id':ident,'offset_seconds':offset,'duration_seconds':300,'plan':str(path),'sha256':sha(path)})
    plan={**common,'id':'SF-PILOT-01-ATTEMPT-03','public_id':'SF-001','duration_seconds':3600,'faults':faults,
        'status':'ONE_CONTEST_PILOT_PREREGISTERED','capture_offsets_seconds':OFFSETS,
        'trace_source':'JAEGER_OPENSEARCH_STORAGE','trace_offsets_seconds':[5,60,120,240],
        'trace_completion_deadlines_seconds':[60,120,180,300],'quality_assessment_deadline_after_chunk_seconds':420,
        'fault_onset_tolerance_seconds':2,'scheduler':'WORKER_LOCAL_UTC_SYSTEMD_10ms','authorization_lease_seconds':4,'maximum_clock_bound_seconds':0.5,'metric_chunk_policy':'exact half-open [start,end); retain all raw captures','recovery_check_offsets_seconds':{'SERVICE':1260,'POD':2580},
        'sampling_exclusions':None,'expected_request_policy':'all ordinary sent requests started in the hour; separate out-of-window and not-sent records; exact run window + Trace/Span identity',
        'interim_request_boundary_guard_seconds':1,'final_request_boundary_guard_seconds':0,
        'private_edge_evidence':{'width_seconds':1,'read_offset_seconds':120,'completion_deadline_seconds':180,
            'identity_rule':'same Trace ID and real Proxy Server Span; client and server timestamps both within one second of the same global edge',
            'reported_separately_from_public_matches':True,'public_export_of_guard_rows':False,'timestamp_rewriting':False,
            'unexplained_nonboundary_missing_requests':'FAIL'},
        'stop_conditions':['actual_environment_failure','any_collector_receiver_refused_increment','collector_working_set_ge_380MiB',
            'collector_or_target_identity_changed','storage_freshness_gt_60s','independent_Image_or_Cart_HTTP_failure','mature_unexplained_request_gap','capture_deadline_failure','rollback_failure'],
        'public_export':{'event_time_half_open_window_only':True,'missing_values':None,'preserve_counter_values':True,
            'trace_version':'immutable +240 audit snapshot; earlier versions retained and ordinary late spans still fail quality',
            'source_private_information_policy':'reject leakage; do not silently drop rows','neutral_client_metadata':'User-Agent only; original workflows/rates unchanged'},
        'annotation_policy':{'only_public_observations':True,'independent_no_fault_reference':'collector repair VAL1/VAL2; same config and rates; older uptime is a comparability limitation',
            'gt_status':'DRAFT_PENDING_HUMAN_REVIEW'},'maximum_new_case_attempts':1,'faults_injected_before_plan':0}
    save(OPS/'plans/SF-PILOT-01-ATTEMPT-03.json',plan)
    save(PROD/'plans/manifest.json',{'plan_sha256':sha(OPS/'plans/SF-PILOT-01-ATTEMPT-03.json'),'source_sha256':sources,'fault_plan_sha256':{f['plan']:f['sha256'] for f in faults},'at_utc':now()})
    print(json.dumps({'status':plan['status'],'plan_sha256':sha(OPS/'plans/SF-PILOT-01-ATTEMPT-03.json')}))
if __name__=='__main__':main()
