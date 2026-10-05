"""Departure time bins and the configured speed profile."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.config import SpeedBin
from app.core.timeutil import minute_of_day_ist


def time_bin_index(profile: Sequence[SpeedBin], dt: datetime) -> int:
    minute = minute_of_day_ist(dt)
    idx = 0
    for i, b in enumerate(profile):
        if minute >= b.start_min:
            idx = i
    return idx


def speed_kmh(profile: Sequence[SpeedBin], dt: datetime) -> float:
    return profile[time_bin_index(profile, dt)].kmh


def free_flow_kmh(profile: Sequence[SpeedBin]) -> float:
    return max(b.kmh for b in profile)
