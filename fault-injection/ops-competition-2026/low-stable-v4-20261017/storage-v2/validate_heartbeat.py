"""Validate a v2 storage heartbeat against the same 5 GB decision contract."""

from datetime import datetime

from storage_forecast_gate import DISKS, EMERGENCY_MARGIN, FLOOR


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def validate(value, run_id, plan_sha, checked_at, monitor_sha, gate_sha):
    if (value.get('run_id') != run_id or value.get('plan_sha256') != plan_sha or
            value.get('status') != 'DYNAMIC_STORAGE_PASS' or
            value.get('source_sha256') != monitor_sha or
            value.get('storage_gate_sha256') != gate_sha or
            not 0 <= checked_at - timestamp(value['at_utc']) <= 30):
        raise RuntimeError('Five-host v2 heartbeat missing, stale or foreign')
    decision = value.get('decision', {})
    if (decision.get('status') != 'DYNAMIC_STORAGE_PASS' or
            decision.get('floor_bytes') != FLOOR or
            decision.get('fault_permission') is not False):
        raise RuntimeError('Five-host v2 decision contract changed')
    snapshot, forecast = value.get('snapshot'), value.get('forecast')
    if (not isinstance(snapshot, dict) or not isinstance(forecast, dict) or
            set(snapshot) != set(DISKS) or set(forecast) != set(DISKS)):
        raise RuntimeError('Five-host v2 disk universe incomplete')
    pending = decision.get('pending_forecast_breaches')
    if not isinstance(pending, dict):
        raise RuntimeError('Five-host v2 pending forecasts missing')
    expected = set()
    for host, disks in DISKS.items():
        for disk in disks:
            free = snapshot[host].get(disk)
            spend = forecast[host].get(disk)
            if (type(free) is not int or type(spend) is not int or
                    spend < 0 or free < FLOOR):
                raise RuntimeError('Five-host actual 5 GB floor or metric failed')
            if free - spend < FLOOR:
                if free < FLOOR + EMERGENCY_MARGIN:
                    raise RuntimeError('Five-host emergency forecast failed')
                key = host + ':' + disk
                expected.add(key)
                if type(pending.get(key)) is not int or not 1 <= pending[key] <= 2:
                    raise RuntimeError('Five-host forecast confirmation invalid')
    if set(pending) != expected:
        raise RuntimeError('Five-host pending forecast does not match snapshot')
    return {'status': 'FIVE_HOST_V2_HEARTBEAT_VERIFIED',
            'pending_forecast_breaches': pending}
