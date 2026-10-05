"""Time helpers. Storage is UTC, the service day and display are Asia/Kolkata."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BIN_S = 900


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return dt.astimezone(UTC)


def to_ist(dt: datetime) -> datetime:
    return ensure_utc(dt).astimezone(IST)


def service_date_of(dt: datetime) -> date:
    return to_ist(dt).date()


def day_start_utc(service_date: date) -> datetime:
    """00:00 IST of the service date, as UTC."""
    return datetime.combine(service_date, time(0, 0), tzinfo=IST).astimezone(UTC)


def ist_at(service_date: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    base = datetime.combine(service_date, time(0, 0), tzinfo=IST)
    return (base + timedelta(hours=hour, minutes=minute, seconds=second)).astimezone(UTC)


def seconds_since_day_start(dt: datetime, service_date: date) -> int:
    return int((ensure_utc(dt) - day_start_utc(service_date)).total_seconds())


def from_day_seconds(service_date: date, seconds: int) -> datetime:
    return day_start_utc(service_date) + timedelta(seconds=int(seconds))


def minute_of_day_ist(dt: datetime) -> int:
    local = to_ist(dt)
    return local.hour * 60 + local.minute


def depart_bin(dt: datetime) -> int:
    """15 minute departure bin index within the IST day (0..95)."""
    return minute_of_day_ist(dt) * 60 // BIN_S


def weekday_type(d: date) -> str:
    return "weekend" if d.weekday() >= 5 else "weekday"


def iso_ist(dt: datetime) -> str:
    """ISO 8601 with the +05:30 offset, seconds precision."""
    return to_ist(dt).replace(microsecond=0).isoformat()


def utcnow() -> datetime:
    return datetime.now(UTC)


def seconds_until_next_ist_midnight(now: datetime) -> int:
    local = to_ist(now)
    nxt = datetime.combine(local.date() + timedelta(days=1), time(0, 0), tzinfo=IST)
    return max(1, int((nxt - local).total_seconds()))
