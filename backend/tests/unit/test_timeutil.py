from __future__ import annotations

from datetime import UTC, date, datetime

from app.config import Settings
from app.core.speed import speed_kmh, time_bin_index
from app.core.timeutil import (
    day_start_utc,
    depart_bin,
    iso_ist,
    ist_at,
    seconds_since_day_start,
    seconds_until_next_ist_midnight,
    service_date_of,
    weekday_type,
)


def test_ist_day_boundaries() -> None:
    d = date(2026, 10, 6)
    assert day_start_utc(d) == datetime(2026, 10, 5, 18, 30, tzinfo=UTC)
    assert service_date_of(datetime(2026, 10, 5, 18, 29, tzinfo=UTC)) == date(2026, 10, 5)
    assert service_date_of(datetime(2026, 10, 5, 18, 30, tzinfo=UTC)) == d
    assert seconds_since_day_start(ist_at(d, 9, 30), d) == 9 * 3600 + 1800
    assert iso_ist(ist_at(d, 9)) == "2026-10-06T09:00:00+05:30"
    assert depart_bin(ist_at(d, 9, 14)) == 36 and depart_bin(ist_at(d, 9, 15)) == 37
    assert weekday_type(date(2026, 10, 10)) == "weekend" and weekday_type(d) == "weekday"
    assert seconds_until_next_ist_midnight(ist_at(d, 23, 59)) == 60


def test_speed_bins(settings: Settings) -> None:
    d = date(2026, 10, 6)
    prof = settings.speed_profile_json
    assert speed_kmh(prof, ist_at(d, 3)) == 38
    assert speed_kmh(prof, ist_at(d, 8)) == 15
    assert speed_kmh(prof, ist_at(d, 18)) == 13
    assert time_bin_index(prof, ist_at(d, 22)) == 5
