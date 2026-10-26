#!/usr/bin/env python3
"""Post-rehearsal ten-case disk planning; original peak reservations stay intact."""

import json
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
FLOOR = 20_000_000_000
GIB = 1024**3
PEAK = {'master': 34 * GIB, 'w2': 31 * GIB, 'w3': 29 * GIB}


def count_until_peak_blocked(available, demand, net):
    count = 0
    for _ in range(10):
        if available - demand < FLOOR:
            break
        count += 1
        available -= net
    return count


def main():
    p = HERE / 'reports/nofault30-summary.json'
    data = json.loads(p.read_text())
    if data['terminal']['status'] != 'NOFAULT_30MIN_CAPTURE_PASS':
        raise RuntimeError('Actual rehearsal capture did not pass')
    if data['dual_archive']['status'] != 'NOFAULT_DUAL_ARCHIVE_PASS':
        raise RuntimeError('Cannot size a nonverified archive')
    net = data['source_archive_manifest']['bytes']
    stress = (net * 5 + 3) // 4
    free = {'master': data['master_data_free_after'],
            'w2': data['w2']['data_free_bytes'],
            'w3': data['w3']['data_free_bytes']}
    rows = {}
    for host in PEAK:
        rows[host] = {
            'free_after_rehearsal_bytes': free[host],
            'retained_old_peak_demand_bytes': PEAK[host],
            'next_case_peak_residual_bytes': free[host] - PEAK[host],
            'next_case_eligible_by_storage_only': free[host] - PEAK[host] >= FLOOR,
            'additional_cases_possible_at_measured_net_with_old_peak': count_until_peak_blocked(
                free[host], PEAK[host], net),
            'ten_case_additional_capacity_linear_bytes': max(
                0, FLOOR + 9 * net + PEAK[host] - free[host]),
            'ten_case_additional_capacity_25pct_bytes': max(
                0, FLOOR + 9 * stress + PEAK[host] - free[host]),
            'free_after_ten_linear_without_expansion_bytes': free[host] - 10 * net,
        }
    result = {
        'status': 'MEASURED_NOFAULT_CAPACITY_FORECAST_NOT_FORMAL_ADMISSION',
        'floor_bytes': FLOOR,
        'source_archive_bytes_each_worker': net,
        'per_future_case_net_sensitivity_25pct_bytes': stress,
        'preview_peak_work_bytes': json.loads(
            (HERE / 'reports/preview-export-v2-result.json').read_text()
        )['guard']['peak_bytes']['work'],
        'preview_peak_product_bytes': json.loads(
            (HERE / 'reports/preview-export-v2-result.json').read_text()
        )['guard']['peak_bytes']['product'],
        'hosts': rows,
        'cautions': [
            'Only one no-fault 30-minute run has been measured.',
            'New 3-6-event cases may exceed this trace/CPU/disk rate.',
            'Future case admission must reserve peak headroom on every host before injection.',
            'Historical archives remain protected; no deletion assumed.',
        ],
    }
    output = HERE / 'reports/capacity-after-rehearsal.json'
    if output.exists():
        raise RuntimeError('No overwrite')
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({
        'worker2_measured_additional_cases': rows['w2']['additional_cases_possible_at_measured_net_with_old_peak'],
        'worker2_25pct_extra_GB': round(rows['w2']['ten_case_additional_capacity_25pct_bytes'] / 1e9, 3),
        'worker3_25pct_extra_GB': round(rows['w3']['ten_case_additional_capacity_25pct_bytes'] / 1e9, 3),
    }))


if __name__ == '__main__':
    main()
