"""Builds and tears down the process wide AppContext."""

from __future__ import annotations

from datetime import date

import httpx
from redis.asyncio import Redis

from app.adapters.factory import build_datasource, build_notifier
from app.config import Settings
from app.core.clock import Clock, SimAwareClock
from app.core.timeutil import service_date_of, utcnow
from app.db.session import make_engine, make_session_factory
from app.jobs.context import AppContext


def _today_wall() -> date:
    return service_date_of(utcnow())


async def create_context(
    settings: Settings, *, clock: Clock | None = None, pool_size: int = 10
) -> AppContext:
    engine = make_engine(settings.database_url, pool_size=pool_size)
    factory = make_session_factory(engine)
    # Socket timeout longer than any blocking command (the event consumer blocks for up to 5 s).
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_timeout=30,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
    http = httpx.AsyncClient(timeout=settings.http_timeout_s)
    the_clock = clock or SimAwareClock(redis)
    source = build_datasource(settings, _today_wall, redis)
    notifier = build_notifier(settings, redis, http)
    return AppContext(
        base_settings=settings,
        engine=engine,
        factory=factory,
        redis=redis,
        source=source,
        clock=the_clock,
        notifier=notifier,
        http=http,
    )


async def close_context(ctx: AppContext) -> None:
    await ctx.source.aclose()
    await ctx.http.aclose()
    await ctx.redis.aclose()
    await ctx.engine.dispose()
