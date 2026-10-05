"""Liveness, readiness and Prometheus metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.api.deps import Ctx
from app.core.circuit import CircuitBreaker
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger("api.health")
PROVIDERS = ("here_routing", "here_matrix", "osrm")


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(ctx: Ctx, response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    ok = True
    try:
        async with ctx.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        log.warning("readyz_db_failed", error=str(exc))
        checks["database"] = "error"
        ok = False
    try:
        await ctx.redis.ping()
        checks["redis"] = "ok"
        settings = ctx.base_settings
        circuits = {}
        for p in PROVIDERS:
            breaker = CircuitBreaker(
                ctx.redis, p, threshold=settings.breaker_failure_threshold, open_s=settings.breaker_open_s
            )
            circuits[p] = await breaker.state()
        checks["providers"] = circuits
    except Exception as exc:
        log.warning("readyz_redis_failed", error=str(exc))
        checks["redis"] = "error"
        ok = False
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "unavailable", "checks": checks}


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
