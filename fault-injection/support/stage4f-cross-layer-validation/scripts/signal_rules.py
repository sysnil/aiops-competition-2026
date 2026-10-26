"""Pre-registered Stage4F signal anomaly rules; None means unobservable."""

from statistics import median


def percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    point = (len(ordered) - 1) * fraction
    lo = int(point)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (point - lo)


def resource_metric(before, during):
    if len(before) < 20 or len(during) < 10:
        return None
    baseline = median(before)
    observed = median(during)
    mad = median(abs(value - baseline) for value in before)
    limit = max(3 * 1.4826 * mad, 0.50 * max(abs(baseline), 1e-9))
    return abs(observed - baseline) > limit


def new_events(before_count, during_count, before_seconds, during_seconds):
    if None in (before_count, during_count) or before_seconds <= 0 or during_seconds <= 0:
        return None
    return during_count > 0 and during_count / during_seconds > before_count / before_seconds


def error_log_rate(before_errors, during_errors, before_seconds, during_seconds):
    if None in (before_errors, during_errors) or before_seconds <= 0 or during_seconds <= 0:
        return None
    return during_errors / during_seconds > 2 * before_errors / before_seconds + 0.01


def latency_p95(before, during):
    if len(before) < 30 or len(during) < 30:
        return None
    baseline = percentile(before, 0.95)
    observed = percentile(during, 0.95)
    return observed > max(1.5 * baseline, baseline + 10)


def network_drop_fraction(before_drops, before_packets, during_drops, during_packets):
    if None in (before_drops, before_packets, during_drops, during_packets):
        return None
    if before_packets < 100 or during_packets < 100:
        return None
    return during_drops / during_packets > before_drops / before_packets + 0.01
