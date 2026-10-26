from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def robust_score(value: float, values: Iterable[float]) -> tuple[float, float, float]:
    sample = list(values)
    if not sample:
        return 0.0, 0.0, 0.0
    median = statistics.median(sample)
    mad = statistics.median(abs(x - median) for x in sample)
    scale = max(1.4826 * mad, abs(median) * 0.05, 1e-9)
    return (value - median) / scale, median, mad


def percentile(values: Iterable[float], q: float) -> float | None:
    xs = sorted(values)
    if not xs:
        return None
    position = (len(xs) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(xs) - 1)
    fraction = position - lower
    return xs[lower] * (1 - fraction) + xs[upper] * fraction

