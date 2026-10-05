"""Named jobs runnable by the scheduler and by POST /jobs/{name}/run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.jobs.actuals import eta_actuals
from app.jobs.calibrate import calibrate_factors
from app.jobs.estimate import estimate_day
from app.jobs.ingest import ingest_date
from app.jobs.purge import purge_here_cache
from app.jobs.runner import JobRunHandle


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    fn: Callable[[JobRunHandle], Awaitable[dict[str, Any]]]
    default_date: str  # "tomorrow", "today" or "none"
    description: str
    lock_ttl_s: int = 900


async def _ingest(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    return await ingest_date(h.ctx, h.service_date)


async def _estimate(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    return await estimate_day(h.ctx, h.service_date)


async def _actuals(h: JobRunHandle) -> dict[str, Any]:
    return await eta_actuals(h.ctx)


async def _calibrate(h: JobRunHandle) -> dict[str, Any]:
    return await calibrate_factors(h.ctx)


async def _purge(h: JobRunHandle) -> dict[str, Any]:
    return await purge_here_cache(h.ctx)


REGISTRY: dict[str, JobSpec] = {
    "ingest": JobSpec("ingest", _ingest, "tomorrow", "Pull trips, roster, vehicles and hubs into a snapshot"),
    "estimate_day": JobSpec(
        "estimate_day", _estimate, "tomorrow", "Estimate trip legs, compatible deadhead edges and hub legs"
    ),
    "eta_actuals": JobSpec("eta_actuals", _actuals, "none", "Join actual pickup and drop times to estimates"),
    "calibrate_factors": JobSpec(
        "calibrate_factors", _calibrate, "none", "Rebuild the factor table from eta_log"
    ),
    "purge_here_cache": JobSpec("purge_here_cache", _purge, "none", "Delete HERE derived data past 30 days"),
}


def register(spec: JobSpec) -> None:
    REGISTRY[spec.name] = spec


def resolve_date(spec: JobSpec, today: date, explicit: date | None) -> date | None:
    if explicit is not None:
        return explicit
    if spec.default_date == "tomorrow":
        return today + timedelta(days=1)
    if spec.default_date == "today":
        return today
    return None
