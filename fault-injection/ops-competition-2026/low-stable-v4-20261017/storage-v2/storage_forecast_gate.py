"""Confirm transient disk-rate forecasts without relaxing the 5 GB floor."""

from collections import defaultdict


FLOOR = 5_000_000_000
EMERGENCY_MARGIN = 2_000_000_000
CONFIRMATIONS = 3
DISKS = {
    'master': ('root_free_bytes', 'data_free_bytes'),
    'worker1': ('root_free_bytes', 'data_free_bytes'),
    'worker2': ('root_free_bytes', 'data_free_bytes'),
    'worker3': ('root_free_bytes', 'data_free_bytes'),
    'vm1': ('root_free_bytes',),
}


class StorageForecastGate:
    def __init__(self):
        self.pending = defaultdict(int)

    def evaluate(self, snapshot, forecast):
        if set(snapshot) != set(DISKS) or set(forecast) != set(DISKS):
            return self._stop('MISSING_HOST_OR_FORECAST')
        current = {}
        for host, disks in DISKS.items():
            for disk in disks:
                free = snapshot[host].get(disk)
                spend = forecast[host].get(disk)
                if (type(free) is not int or type(spend) is not int or
                        spend < 0):
                    return self._stop('INVALID_SNAPSHOT_OR_FORECAST_' + host + '_' + disk)
                if free < FLOOR:
                    return self._stop('ACTUAL_FIVE_GB_FLOOR_' + host + '_' + disk)
                if free - spend < FLOOR:
                    if free < FLOOR + EMERGENCY_MARGIN:
                        return self._stop('EMERGENCY_FORECAST_' + host + '_' + disk)
                    key = host + ':' + disk
                    current[key] = self.pending[key] + 1
        self.pending = defaultdict(int, current)
        confirmed = [key for key, count in current.items() if count >= CONFIRMATIONS]
        if confirmed:
            return self._stop('SUSTAINED_FORECAST_' + confirmed[0])
        return {'status': 'DYNAMIC_STORAGE_PASS', 'floor_bytes': FLOOR,
                'fault_permission': False,
                'pending_forecast_breaches': dict(current),
                'forecast_confirmation_rule': '3 consecutive samples at 15s; actual floor immediate'}

    def _stop(self, reason):
        return {'status': 'STOP_NEW_WORK', 'reason': reason,
                'floor_bytes': FLOOR, 'fault_permission': False,
                'pending_forecast_breaches': dict(self.pending)}
