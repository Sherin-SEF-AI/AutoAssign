"""Builds a LegEstimator wired to the configured providers."""

from __future__ import annotations

import httpx
from redis.asyncio import Redis

from app.adapters.here.budget import BudgetGuard, caps_from_settings
from app.adapters.here.client import HereClient
from app.adapters.osrm.client import OsrmClient
from app.config import Settings
from app.core.circuit import CircuitBreaker
from app.db.session import SessionFactory
from app.eta.estimator import LegEstimator
from app.eta.factors import load_factor_table


def budget_guard(settings: Settings, redis: Redis) -> BudgetGuard:
    return BudgetGuard(
        redis,
        caps_from_settings(
            settings.here_routing_daily_cap,
            settings.here_matrix_daily_cap,
            settings.here_matrix_elements_daily_cap,
        ),
    )


def breaker(settings: Settings, redis: Redis, provider: str) -> CircuitBreaker:
    return CircuitBreaker(
        redis, provider, threshold=settings.breaker_failure_threshold, open_s=settings.breaker_open_s
    )


def here_client(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> HereClient | None:
    if not settings.here_available or not settings.here_api_key:
        return None
    return HereClient(
        http,
        api_key=settings.here_api_key,
        routing_url=settings.here_routing_url,
        matrix_url=settings.here_matrix_url,
        budget=budget_guard(settings, redis),
        routing_breaker=breaker(settings, redis, "here_routing"),
        matrix_breaker=breaker(settings, redis, "here_matrix"),
        retries=settings.http_retries,
        backoff_base_s=settings.http_backoff_base_s,
        timeout_s=settings.http_timeout_s,
    )


def osrm_client(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> OsrmClient | None:
    if not settings.osrm_available or not settings.osrm_url:
        return None
    return OsrmClient(
        http,
        base_url=settings.osrm_url,
        breaker=breaker(settings, redis, "osrm"),
        retries=settings.http_retries,
        backoff_base_s=settings.http_backoff_base_s,
        timeout_s=settings.http_timeout_s,
    )


async def build_estimator(
    settings: Settings,
    factory: SessionFactory,
    redis: Redis,
    http: httpx.AsyncClient,
    *,
    use_here: bool = True,
    persist: bool = True,
) -> LegEstimator:
    async with factory() as session:
        factors = await load_factor_table(session, settings)
    return LegEstimator(
        settings=settings,
        factory=factory,
        redis=redis,
        factors=factors,
        here=here_client(settings, redis, http) if use_here else None,
        osrm=osrm_client(settings, redis, http),
        persist=persist,
    )
