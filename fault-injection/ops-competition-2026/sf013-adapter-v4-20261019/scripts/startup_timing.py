"""Prospective identity and startup time budgets for a new Attempt."""

from datetime import datetime


FROZEN_IDENTITY_LIMIT_SECONDS = 180
MAX_AGE_AT_REGISTRATION_SECONDS = 30
MAX_AGE_AT_START_SECONDS = 150
T0_START_MIN_SECONDS = 90
T0_START_MAX_SECONDS = 240


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def registration_identity_age(identity_utc, at_epoch):
    age = at_epoch - stamp(identity_utc)
    if not 0 <= age <= MAX_AGE_AT_REGISTRATION_SECONDS:
        raise RuntimeError('Refresh Pod/PID identity immediately before registration')
    return age


def startup_budget(identity_utc, start_utc, at_epoch):
    identity_age = at_epoch - stamp(identity_utc)
    t0_remaining = stamp(start_utc) - at_epoch
    if not T0_START_MIN_SECONDS <= t0_remaining <= T0_START_MAX_SECONDS:
        raise RuntimeError('T0 is outside the frozen startup window')
    if not 0 <= identity_age <= MAX_AGE_AT_START_SECONDS:
        raise RuntimeError('Pod/PID identity lacks thirty-second freshness margin')
    if identity_age > FROZEN_IDENTITY_LIMIT_SECONDS:
        raise RuntimeError('Frozen identity gate exceeded')
    return {'identity_age_seconds': identity_age,
            't0_remaining_seconds': t0_remaining,
            'frozen_identity_limit_seconds': FROZEN_IDENTITY_LIMIT_SECONDS,
            'identity_margin_seconds': FROZEN_IDENTITY_LIMIT_SECONDS - identity_age}
