#!/usr/bin/env python3
"""Apply the Stage4F executable gate without freezing or injecting a fault."""

import argparse
import copy
import json
from pathlib import Path
import subprocess
import uuid

import yaml

from contracts import sha256
from network_path_precheck import validate as validate_network_path
from verify_live_conversion import verify as verify_conversion
from validate_openrca_smoke import validate as validate_openrca_smoke


RUNS = {'microrca-a': 'CrossLayer-MicroRCA',
        'microrca-b': 'CrossLayer-MicroRCA',
        'simple-a': 'CrossLayer-SimpleRCA',
        'simple-b': 'CrossLayer-SimpleRCA',
        'openrca-b': 'OpenRCA-XL',
        'microirc-original-b': 'MicroIRC-original',
        'microirc-xl-b': 'MicroIRC-XL'}


def read_json(path):
    return json.loads(path.read_text())


def validate_openrca_pair(live_bundle, clean_prediction, clean_audit,
                          fixture_bundle, fixture_prediction, fixture_audit,
                          fixture_construction):
    live = read_json(live_bundle)
    clean = read_json(clean_prediction)
    clean_check = read_json(clean_audit)
    fixture = read_json(fixture_bundle)
    construction = read_json(fixture_construction)
    if (construction.get('status') != 'CONTROLLED_PUBLIC_ANOMALY_FIXTURE' or
            construction.get('source_bundle_sha256') != sha256(live_bundle) or
            construction.get('fixture_bundle_sha256') != sha256(fixture_bundle) or
            clean.get('input_sha256') != sha256(live_bundle) or
            clean.get('method_kind') != 'adapted_llm_agent'):
        raise ValueError('OpenRCA live/fixture source provenance is invalid')
    if (uuid.UUID(fixture['public_case_id']).version != 4 or
            fixture['public_case_id'] == live['public_case_id'] or
            construction.get('public_case_id') != fixture['public_case_id']):
        raise ValueError('Controlled fixture anonymous identity is invalid')
    eligible = []
    for entity, modalities in live['signals'].items():
        if entity.startswith('SERVICE/'):
            count = sum(signal.get('observed') is True
                        for rows in modalities.values() for signal in rows)
            if count >= 4:
                eligible.append((-count, entity))
    if not eligible or construction.get('selected_public_entity') != min(eligible)[1]:
        raise ValueError('Controlled fixture selection does not follow public rule')
    expected = copy.deepcopy(live)
    expected['public_case_id'] = fixture['public_case_id']
    changed = 0
    for rows in expected['signals'][min(eligible)[1]].values():
        for signal in rows:
            if signal.get('observed') is True:
                signal['abnormal'] = True
                changed += 1
    if (fixture != expected or changed != construction.get('signals_marked_abnormal') or
            changed < 4):
        raise ValueError('Controlled fixture differs from the declared public edit')
    if clean.get('status') == 'PASS':
        clean_validation = validate_openrca_smoke(live_bundle, clean_prediction, clean_audit)
        clean_status = clean_validation['status']
    elif clean.get('status') == 'NO_PREDICTION':
        llm = clean.get('llm_metadata', {})
        if (clean.get('ranking') != [] or clean.get('root_layer') is not None or
                clean.get('root_entity') is not None or
                clean_check.get('status') !=
                'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_VALID_ABSTENTION' or
                clean_check.get('input_sha256') != sha256(live_bundle) or
                clean_check.get('prediction_sha256') != sha256(clean_prediction) or
                clean_check.get('endpoint_host') != llm.get('endpoint_host') or
                llm.get('transport') != 'https' or
                not all(clean_check.get(key) is value for key, value in (
                    ('public_read_allowed', True), ('home_read_denied', True),
                    ('other_temp_read_denied', True), ('gt_read', False)))):
            raise ValueError('OpenRCA clean public abstention is not auditable')
        clean_status = 'VALID_CLEAN_ABSTENTION'
    else:
        raise ValueError('OpenRCA live inference neither ranked nor cleanly abstained')
    fixture_validation = validate_openrca_smoke(
        fixture_bundle, fixture_prediction, fixture_audit)
    clean_llm = clean.get('llm_metadata', {})
    fixture_llm = read_json(fixture_prediction).get('llm_metadata', {})
    if (clean_llm.get('endpoint_host') != fixture_llm.get('endpoint_host') or
            clean_llm.get('model') != fixture_llm.get('model')):
        raise ValueError('Live and fixture endpoint/model differ')
    return {'clean_live_status': clean_status,
            'fixture_status': fixture_validation['status'],
            'fixture_ranking_count': fixture_validation['ranking_count'],
            'fixture_evidence_references': fixture_validation['public_evidence_references'],
            'endpoint_host': fixture_llm['endpoint_host'],
            'model': fixture_llm['model'],
            'fixture_bundle_sha256': sha256(fixture_bundle),
            'fixture_prediction_sha256': sha256(fixture_prediction),
            'fixture_audit_sha256': sha256(fixture_audit),
            'fixture_construction_sha256': sha256(fixture_construction),
            'clean_prediction_sha256': sha256(clean_prediction),
            'clean_audit_sha256': sha256(clean_audit)}


def assess(root, conversion, before, during, proof_file, probe, f01_audit,
           runs_dir, residue_file, clean_audit, fixture_bundle,
           fixture_prediction, fixture_audit, fixture_construction):
    blockers = []
    if any((root / name).exists() for name in
           ('freeze-manifest.json', 'cases', 'evaluator_gt')):
        raise ValueError('Fault, GT or freeze artifacts already exist')
    jobs = json.loads(subprocess.check_output(['kubectl', 'get', 'jobs', '-A', '-o', 'json'],
                                               text=True, timeout=30))
    if any('stage4f' in item['metadata']['name'].lower() for item in jobs['items']):
        raise ValueError('Stage4F Job still exists')
    residue = read_json(residue_file)
    if residue.get('status') != 'HOST_RESIDUE_PASS' or len(residue.get('hosts', {})) < 5:
        raise ValueError('Five-host residual fault audit did not pass')
    conversion_result = verify_conversion(conversion, before, during, proof_file, probe)
    if conversion_result['status'] != 'STAGE4F_LIVE_CONVERSION_VERIFIED' or (
            not conversion_result['f02_target_coverage'] or
            conversion_result['public_private_leak_scan'] != 'PASS'):
        raise ValueError('Live conversion, F02 coverage or leak gate failed')
    proof = read_json(proof_file)
    path_result = validate_network_path(proof)
    if path_result['status'] != 'NETWORK_PATH_PRECHECK_PASS':
        raise ValueError('Fresh F04 route proof did not pass')
    f01 = read_json(f01_audit)
    if f01.get('status') != 'F01_PRECONDITION_PASS' or len(f01.get('pods', [])) != 2:
        raise ValueError('F01 two-replica precondition did not pass')
    provenance = read_json(root / 'source/provenance.json')
    source_root = Path(provenance['source_archive_root'])
    for item in provenance['repositories']:
        if sha256(source_root / item['archive']) != item['sha256']:
            raise ValueError('Author source archive hash changed: ' + item['algorithm'])
    eligibility = yaml.safe_load((root / 'design/scoring-contract.yaml').read_text())[
        'method_eligibility']
    if eligibility['nonmandatory_when_label_free_inference_unavailable'] != [
            'MicroIRC-original', 'MicroIRC-XL']:
        raise ValueError('MicroIRC eligibility contract changed')
    bundle_hashes = {
        'a': sha256(conversion / 'public/variant-a/bundle.json'),
        'b': sha256(conversion / 'public/variant-b/bundle.json'),
    }
    methods = {}
    for name, expected_algorithm in RUNS.items():
        path = runs_dir / ('stage4f-final-' + name + '.json')
        record = read_json(path)
        variant = name[-1]
        if record.get('algorithm') != expected_algorithm or (
                record.get('input_sha256') != bundle_hashes[variant]):
            raise ValueError('Method result algorithm or input hash mismatch: ' + name)
        methods[name] = {'status': record['status'],
                         'root_entity': record.get('root_entity'),
                         'ranking_count': len(record.get('ranking', [])),
                         'sha256': sha256(path)}
    for name in ('microrca-a', 'microrca-b', 'simple-a', 'simple-b'):
        if methods[name]['status'] != 'PASS' or methods[name]['ranking_count'] < 1:
            blockers.append('ADAPTED_METHOD_LIVE_INPUT_' + name.upper())
    for name in ('microirc-original-b', 'microirc-xl-b'):
        if methods[name]['status'] == 'BLOCKED_PREREQUISITE':
            if methods[name]['ranking_count']:
                raise ValueError('Blocked MicroIRC method emitted a ranking')
        elif methods[name]['status'] != 'PASS':
            blockers.append('MICROIRC_STATUS_INVALID_' + name.upper())
    openrca_evidence = validate_openrca_pair(
        conversion / 'public/variant-b/bundle.json',
        runs_dir / 'stage4f-final-openrca-b.json', clean_audit,
        fixture_bundle, fixture_prediction, fixture_audit, fixture_construction)
    code = {str(path.relative_to(root)): sha256(path)
            for folder in ('design', 'scripts', 'algorithms')
            for path in sorted((root / folder).glob('*')) if path.is_file()}
    return {'status': 'READY_TO_FREEZE' if not blockers else 'PRE_FREEZE_BLOCKED',
            'blockers': blockers, 'faults_injected': 0, 'contracts_frozen': False,
            'live_converter': conversion_result,
            'f01_precondition': f01['status'], 'f04_pre_fault_route_proof': path_result,
            'host_residue': residue['status'],
            'microirc_nonmandatory': eligibility[
                'nonmandatory_when_label_free_inference_unavailable'],
            'method_smoke': methods,
            'openrca_real_endpoint_evidence': openrca_evidence,
            'public_bundle_sha256': bundle_hashes,
            'source_provenance_sha256': sha256(root / 'source/provenance.json'),
            'code_sha256': code,
            'f01_audit_sha256': sha256(f01_audit),
            'f04_proof_sha256': sha256(proof_file),
            'host_residue_sha256': sha256(residue_file)}


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'conversion', 'before', 'during', 'proof', 'probe',
                 'f01-audit', 'runs', 'residue', 'openrca-clean-audit',
                 'openrca-fixture', 'openrca-fixture-prediction',
                 'openrca-fixture-audit', 'openrca-fixture-construction', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite executable readiness decision')
    result = assess(args.root, args.conversion, args.before, args.during,
                    args.proof, args.probe, args.f01_audit, args.runs,
                    args.residue, args.openrca_clean_audit,
                    args.openrca_fixture, args.openrca_fixture_prediction,
                    args.openrca_fixture_audit,
                    args.openrca_fixture_construction)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'blockers': result['blockers'],
                      'faults_injected': 0, 'contracts_frozen': False}))


if __name__ == '__main__':
    main()
