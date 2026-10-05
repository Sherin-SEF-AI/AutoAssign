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
from app.jobs.late_booking import drain_queue
from app.jobs.monitor import intraday_monitor
from app.jobs.morning import morning_validate
from app.jobs.publishing import auto_publish, nightly_solve
from app.jobs.purge import purge_here_cache
from app.jobs.runner import JobRunHandle
from app.jobs.solve import solve_date


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


async def _solve(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    _, stats = await solve_date(h.ctx, h.service_date, created_by="job:solve_date", trigger="on_demand")
    return stats


async def _nightly(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    return await nightly_solve(h.ctx, h.service_date)


async def _auto_publish(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    return await auto_publish(h.ctx, h.service_date)


async def _morning(h: JobRunHandle) -> dict[str, Any]:
    assert h.service_date is not None
    return await morning_validate(h.ctx, h.service_date)


async def _monitor(h: JobRunHandle) -> dict[str, Any]:
    return await intraday_monitor(h.ctx)


async def _late_booking(h: JobRunHandle) -> dict[str, Any]:
    processed = await drain_queue(h.ctx)
    return (
        {"processed": len(processed), "events": processed}
        if processed
        else {"skipped": True, "reason": "queue empty"}
    )


REGISTRY: dict[str, JobSpec] = {
    "nightly_solve": JobSpec(
        "nightly_solve", _nightly, "tomorrow", "Graph, estimates, solve, draft version 1"
    ),
    "auto_publish": JobSpec(
        "auto_publish", _auto_publish, "tomorrow", "Publish the latest draft if ops did not touch it"
    ),
    "morning_validate": JobSpec(
        "morning_validate", _morning, "today", "SOC check-ins and absent drivers, repairs"
    ),
    "intraday_monitor": JobSpec(
        "intraday_monitor", _monitor, "none", "Slack per driver from live positions", 280
    ),
    "late_booking": JobSpec("late_booking", _late_booking, "none", "Process queued trip events into repairs"),
    "solve_date": JobSpec("solve_date", _solve, "tomorrow", "Ingest and solve a date into a new draft"),
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
