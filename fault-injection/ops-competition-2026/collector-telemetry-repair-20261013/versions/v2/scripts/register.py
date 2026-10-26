import json,sys
from pathlib import Path
import yaml
from common import *
sys.path.insert(0,'/root/ops-competition-2026/scripts')
from competition_observation import STATE,CONTROLS,MACHINE,PROFILES,PEOPLE,OLD,CG

def main():
    machine=yaml.safe_load(MACHINE.read_text());profile=yaml.safe_load(PROFILES.read_text())['profiles']['MEDIUM']
    assert profile['arrival_rate_rps']==1.4
    paths=list((ROOT/'scripts').glob('*.py'))+[STATE,CONTROLS,MACHINE,PROFILES,PEOPLE,
        OLD/'fixed_control_load_c3.py',OLD/'capture_live_c2.py',OLD/'trace_storage_reader.py',
        OLD/'schedule_trace_completion.py',CG/'schedule_trace_completion_v2.py',CG/'schedule_trace_v2.py',CG/'trace_identity.py',
        Path('/root/ops-competition-2026/scripts/competition_observation.py')]
    plan={'status':'PREREGISTERED_NO_FAULT_TELEMETRY_REPAIR','at_utc':now(),'faults_allowed':False,
        'candidate':{'parameter':'processors.memory_limiter.spike_limit_percentage','old':25,'new':15,
            'container_limit_mib':400,'gomemlimit_mib':320,'hard_limiter_mib':320,'new_soft_limit_mib':260,
            'batch_queue_retry_sampling_changed':False},
        'runs':{'BASE':{'seed':930100,'duration_seconds':300,'configuration':'ORIGINAL'},
                'VAL1':{'seed':931100,'duration_seconds':900,'configuration':'CANDIDATE'},
                'VAL2':{'seed':932100,'duration_seconds':900,'configuration':'CANDIDATE'}},
        'observation_drain_seconds':30,'chunk_seconds':300,'trace_offsets_seconds':[5,60,120,240],
        'trace_completion_deadlines_seconds':[60,120,180,300],
        'workload':{'profile':'MEDIUM','arrival_rate_sessions_per_second':1.4,
            'workflow_weights':{k:v['weight'] for k,v in machine['workflows'].items()},
            'control_streams':[{'name':'image_asset','rate_per_second':1.0,'seed_offset':101},{'name':'cart_view','rate_per_second':2.0,'seed_offset':103}],
            'control_timeout_seconds':8,'control_concurrency_per_stream':{'image_asset':2,'cart_view':4}},
        'criteria':{'all_issued_sampled_request_proxy_match':1.0,'control_http_success_fraction':1.0,
            'state_machine_http_success_fraction':1.0,'proxy_spans_dropped_delta':0,
            'receiver_refused_grpc_delta':0,'receiver_refused_http_delta':0,
            'metrics_export_timeout_log_count':0,'new_refusal_or_export_drop_log_count':0,
            'collector_restarts_or_oom':0,'collector_working_set_stop_mib':380,
            'all_node_source_metric_age_seconds_max':60,'log_and_trace_latest_age_seconds_max':60,
            'all_three_modalities_each_chunk_required':True,'critical_late_spans':0,
            'missing_optional_limiter_failed_retry_counter':'UNKNOWN_NOT_ZERO; cannot independently claim zero failures',
            'unmatched_expected_requests':'FAIL with per-ID evidence; no denominator exclusions after seeing results',
            'fixed_drain_overrun':'FAIL; do not extend window after observation'},
        'source_sha256':{str(p):sha(p) for p in paths},'historical_qualification_unchanged':True,
        'retention_on_success':'keep only after both validations and post-health pass; otherwise rollback',
        'rollback_timer_seconds':5400,'automatic_additional_candidate':'forbidden'}
    save(ROOT/'plans/validation-v2.json',plan);print(json.dumps({'status':plan['status'],'sha256':sha(ROOT/'plans/validation-v2.json')}))
if __name__=='__main__':main()
