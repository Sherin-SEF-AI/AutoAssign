"""Day simulator process (compose profile sim). Follows the clock state set through /admin/sim/*."""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete

from app.config import get_settings
from app.core.clock import SimState, load_sim_state, save_sim_state
from app.core.events import publish
from app.core.logging import configure_logging, get_logger
from app.core.timeutil import day_start_utc, ist_at
from app.db.models import MonitorState, Ping, TripLive
from app.db.queries import published_plan
from app.db.session import session_scope
from app.jobs.context import AppContext
from app.jobs.publishing import auto_publish
from app.jobs.solve import solve_date
from app.runtime import close_context, create_context
from app.simulator.engine import SimEngine

log = get_logger("sim")
TICK_S = 0.5
CONTROL_KEY = "sim:control"
HEARTBEAT_KEY = "sim:heartbeat"


async def ensure_published(ctx: AppContext, day: date) -> None:
    async with ctx.factory() as session:
        if await published_plan(session, day) is not None:
            return
    log.info("sim_preparing_plan", service_date=day.isoformat())
    await solve_date(ctx, day, created_by="sim", trigger="nightly", skip_if_unchanged=True)
    await auto_publish(ctx, day)


async def clear_run(ctx: AppContext, day: date) -> None:
    async with session_scope(ctx.factory) as session:
        await session.execute(delete(TripLive).where(TripLive.service_date == day))
        await session.execute(delete(MonitorState).where(MonitorState.service_date == day))
        await session.execute(
            delete(Ping).where(
                Ping.ts >= day_start_utc(day), Ping.ts < day_start_utc(day + timedelta(days=1))
            )
        )
    for key in (f"absent:{day}", f"monitor:repaired:{day}"):
        await ctx.redis.delete(key)


async def run(ctx: AppContext, stop: asyncio.Event) -> None:
    engine: SimEngine | None = None
    last_clock = 0.0
    while not stop.is_set():
        await ctx.redis.set(HEARTBEAT_KEY, datetime.now(UTC).isoformat(), ex=5)
        if await ctx.redis.get(CONTROL_KEY) == b"reset":
            await ctx.redis.delete(CONTROL_KEY)
            if engine is not None:
                await clear_run(ctx, engine.day)
                log.info("sim_reset", service_date=engine.day.isoformat())
            engine = None
        state: SimState | None = await load_sim_state(ctx.redis)
        if state is None:
            await asyncio.sleep(TICK_S)
            continue
        day = date.fromisoformat(state.service_date)
        try:
            if engine is None or engine.day != day:
                await ensure_published(ctx, day)
                await clear_run(ctx, day)
                engine = SimEngine(
                    ctx,
                    day,
                    disturbances=state.disturbances,
                    ping_interval_s=ctx.base_settings.sim_ping_interval_s,
                )
                await engine.prepare()
                engine.now = max(engine.now, state.anchor_sim)
            engine.disturbances.update(state.disturbances)
            if state.running:
                target = min(state.sim_now(datetime.now(UTC)), ist_at(day, 23, 59))
                await engine.step_to(target)
                if target >= ist_at(day, 23, 59):
                    state.anchor_sim, state.anchor_wall, state.running = target, datetime.now(UTC), False
                    await save_sim_state(ctx.redis, state)
            loop_now = asyncio.get_running_loop().time()
            if loop_now - last_clock >= 1.0:
                last_clock = loop_now
                await ctx.redis.set("sim:stats", json.dumps(engine.stats.as_dict()), ex=60)
                await publish(
                    ctx.redis,
                    "sim.clock",
                    {
                        "sim_time": engine.now.isoformat(),
                        "running": state.running,
                        "speed": state.speed,
                        "service_date": state.service_date,
                    },
                )
        except Exception as exc:
            log.exception("sim_tick_failed", error=str(exc))
            await asyncio.sleep(2)
        await asyncio.sleep(TICK_S)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ctx = await create_context(settings, pool_size=5)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("sim_process_started")
    try:
        await run(ctx, stop)
    finally:
        await close_context(ctx)


if __name__ == "__main__":
    asyncio.run(main())
