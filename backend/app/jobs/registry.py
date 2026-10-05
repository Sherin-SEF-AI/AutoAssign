"""Named jobs runnable by the scheduler and by POST /jobs/{name}/run."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.jobs.ingest import ingest_date
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


REGISTRY: dict[str, JobSpec] = {
    "ingest": JobSpec("ingest", _ingest, "tomorrow", "Pull trips, roster, vehicles and hubs into a snapshot"),
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
