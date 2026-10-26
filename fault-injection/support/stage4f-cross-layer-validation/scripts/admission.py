"""Stage4F case admission checks performed only after prediction freeze."""

from pod_precheck import FAMILIES, MIN_DURING_SAMPLES


LAYERS = {'SERVICE', 'POD', 'NODE', 'NETWORK_PATH'}


def admit(case, bundle, quality, gt, precheck=None):
    reasons = []
    layer, entity = gt.get('root_layer'), gt.get('root_entity')
    if layer not in LAYERS or not isinstance(entity, str) or not entity.startswith(layer + '/'):
        reasons.append('ROOT_NOT_CANONICAL')
    candidates = {item['entity'] for item in bundle.get('candidates', [])}
    if entity not in candidates:
        reasons.append('ROOT_NOT_IN_PUBLIC_CANDIDATES')
    if quality.get('before_seconds', 0) < 600 or quality.get('during_seconds') != 300 or (
            quality.get('after_seconds', 0) < 600):
        reasons.append('TIME_WINDOWS_INVALID')
    for field, reason in (
        ('before_during_comparable', 'TELEMETRY_NOT_COMPARABLE'),
        ('observability_pass', 'OBSERVABILITY_DEFICIT'),
        ('public_private_leak_scan_pass', 'PUBLIC_PRIVATE_LEAK'),
        ('recovery_pass', 'RECOVERY_NOT_PROVED'),
    ):
        if quality.get(field) is not True:
            reasons.append(reason)
    if gt.get('injection_success') is not True:
        reasons.append('INJECTION_NOT_PROVED')
    if layer == 'POD':
        if not precheck or precheck.get('status') != 'POD_CANDIDATE_PRECHECK_PASS':
            reasons.append('POD_PRECHECK_MISSING')
        if (quality.get('pod_uid_before') != quality.get('pod_uid_during') or
                quality.get('pod_uid_before') != gt.get('target_pod_uid')):
            reasons.append('POD_UID_CHANGED_OR_MISMATCHED')
        if quality.get('target_pod_recreated') is not False:
            reasons.append('POD_RECREATED')
        if precheck and precheck.get('candidate') != entity:
            reasons.append('POD_PRECHECK_CANDIDATE_MISMATCH')
        if (not precheck or quality.get('pod_container_id_before') != precheck.get('container_id') or
                quality.get('pod_container_id_during') != precheck.get('container_id')):
            reasons.append('POD_CONTAINER_ID_CHANGED_OR_MISMATCHED')
        during_counts = quality.get('pod_during_metric_sample_counts') or {}
        if any(not isinstance(during_counts.get(metric), int) or
               during_counts[metric] < MIN_DURING_SAMPLES for metric in FAMILIES):
            reasons.append('POD_DURING_CFS_CPU_MEMORY_SAMPLES_INSUFFICIENT')
    if layer == 'NODE' and quality.get('target_node_ready_during') is not True:
        reasons.append('TARGET_NODE_NOT_READY')
    if layer == 'NETWORK_PATH':
        if not precheck or precheck.get('status') != 'NETWORK_PATH_PRECHECK_PASS':
            reasons.append('PATH_PRECHECK_MISSING')
        if precheck and precheck.get('network_path_entity') != entity:
            reasons.append('PATH_PRECHECK_CANDIDATE_MISMATCH')
        if quality.get('path_evidence_comparable') is not True:
            reasons.append('PATH_EVIDENCE_NOT_COMPARABLE')
    if case == 'F01' and quality.get('service_wide_replicas_stable') is not True:
        reasons.append('SERVICE_ROOT_NOT_DISTINGUISHABLE_FROM_POD')
    return {'case_id': case, 'status': 'ADMITTED_CASE' if not reasons else 'INVALID_CASE',
            'reasons': reasons, 'root_layer': layer, 'root_entity': entity}
