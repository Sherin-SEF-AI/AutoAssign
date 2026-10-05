"""Worker entrypoint: APScheduler jobs on Asia/Kolkata time, trip event consumer, sim ticker."""

from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime, time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import start_http_server

from app.config import get_settings
from app.core.clock import load_sim_state
from app.core.logging import configure_logging, get_logger
from app.core.timeutil import IST, service_date_of, to_ist
from app.jobs.context import AppContext
from app.jobs.late_booking import drain_queue
from app.jobs.registry import REGISTRY, resolve_date
from app.jobs.runner import run_job
from app.runtime import close_context, create_context

log = get_logger("worker")

# (job name, cron fields)
SCHEDULE: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ingest", {"hour": 19, "minute": 45}),
    ("nightly_solve", {"hour": 20, "minute": 0}),
    ("auto_publish", {"hour": 21, "minute": 30}),
    ("morning_validate", {"hour": 6, "minute": 0}),
    ("intraday_monitor", {"hour": "6-23", "minute": "*/5"}),
    ("eta_actuals", {"minute": 7}),
    ("calibrate_factors", {"day_of_week": "sun", "hour": 2, "minute": 0}),
    ("purge_here_cache", {"hour": 3, "minute": 0}),
)
MONITOR_END = time(23, 30)


class Worker:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.scheduler = AsyncIOScheduler(timezone=IST)
        self.stopping = asyncio.Event()
        self._sim_marks: dict[str, str] = {}

    async def run_named(self, name: str, service_date: date | None = None, *, wall: bool = True) -> None:
        spec = REGISTRY[name]
        if (
            wall
            and await load_sim_state(self.ctx.redis) is not None
            and name in ("intraday_monitor", "morning_validate")
        ):
            # While a simulation runs these follow simulated time through the sim ticker.
            return
        now = await self.ctx.clock.now()
        if name == "intraday_monitor" and to_ist(now).time() > MONITOR_END:
            return
        day = resolve_date(spec, service_date_of(now), service_date)
        await run_job(self.ctx, name, spec.fn, service_date=day, lock_ttl_s=spec.lock_ttl_s)

    async def sim_tick(self) -> None:
        """Runs monitor, morning validation and actuals on simulated time boundaries."""
        state = await load_sim_state(self.ctx.redis)
        if state is None or not state.running:
            return
        now = await self.ctx.clock.now()
        local = to_ist(now)
        day = service_date_of(now)
        minute = local.hour * 60 + local.minute
        marks = {
            "intraday_monitor": f"{day}:{minute // 5}" if time(6, 0) <= local.time() <= MONITOR_END else None,
            "morning_validate": f"{day}" if local.time() >= time(6, 0) else None,
            "eta_actuals": f"{day}:{local.hour}",
        }
        for name, mark in marks.items():
            if mark is None or self._sim_marks.get(name) == mark:
                continue
            self._sim_marks[name] = mark
            spec = REGISTRY[name]
            await run_job(
                self.ctx,
                name,
                spec.fn,
                service_date=resolve_date(spec, day, None),
                lock_ttl_s=spec.lock_ttl_s,
            )

    async def consume_events(self) -> None:
        while not self.stopping.is_set():
            try:
                processed = await drain_queue(self.ctx, block_s=5)
                if processed:
                    log.info("trip_events_processed", count=len(processed))
            except Exception as exc:
                log.exception("event_consumer_error", error=str(exc))
                await asyncio.sleep(2)

    async def start(self) -> None:
        for name, cron in SCHEDULE:
            self.scheduler.add_job(
                self.run_named,
                CronTrigger(timezone=IST, **cron),
                args=[name],
                id=name,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )
        self.scheduler.add_job(
            self.sim_tick, "interval", seconds=2, id="sim_tick", max_instances=1, coalesce=True
        )
        self.scheduler.start()
        log.info("worker_started", jobs=[n for n, _ in SCHEDULE])
        consumer = asyncio.create_task(self.consume_events())
        await self.stopping.wait()
        self.scheduler.shutdown(wait=False)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(9100)
    ctx = await create_context(settings, pool_size=5)
    worker = Worker(ctx)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stopping.set)
    try:
        await worker.start()
    finally:
        await close_context(ctx)


def next_run_preview(now: datetime) -> dict[str, str]:
    out = {}
    for name, cron in SCHEDULE:
        trig = CronTrigger(timezone=IST, **cron)
        nxt = trig.get_next_fire_time(None, now)
        out[name] = nxt.isoformat() if nxt else ""
    return out


if __name__ == "__main__":
    asyncio.run(main())
