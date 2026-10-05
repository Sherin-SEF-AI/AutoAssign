"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api import problem
from app.api.routers import admin, auth, drivers, events, health, jobs, trips, vehicles
from app.api.security import ensure_admin
from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import HTTP_REQUESTS
from app.db.session import session_scope
from app.jobs.context import AppContext
from app.runtime import close_context, create_context

log = get_logger("api")
API_PREFIX = "/api/v1"


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(cfg.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = ctx is None
        context = ctx or await create_context(cfg)
        app.state.ctx = context
        app.state.tasks = set()
        async with session_scope(context.factory) as session:
            if await ensure_admin(session, cfg):
                log.info("admin_user_created", email=cfg.admin_email)
        try:
            yield
        finally:
            for task in list(app.state.tasks):
                task.cancel()
            if app.state.tasks:
                await asyncio.gather(*app.state.tasks, return_exceptions=True)
            if owned:
                await close_context(context)

    app = FastAPI(
        title="BluRabbit Dispatch API",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        generate_unique_id_function=_operation_id,
    )
    problem.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cfg.web_origin.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        started = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            if request.url.path.endswith("/pings"):
                length = request.headers.get("content-length")
                if length and length.isdigit() and int(length) > cfg.pings_max_body_bytes:
                    response: Response = problem.problem(
                        413, "payload_too_large", "ping batch exceeds the size limit", request
                    )
                    response.headers["X-Request-ID"] = request_id
                    return response
            response = await call_next(request)
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(method=request.method, route=path, status=str(response.status_code)).inc()
            response.headers["X-Request-ID"] = request_id
            if path not in ("/healthz", "/metrics", f"{API_PREFIX}/events/stream"):
                log.info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            return response

    api = APIRouter(prefix=API_PREFIX)
    for module in (auth, trips, drivers, vehicles, jobs, admin, events):
        api.include_router(module.router)
    app.include_router(api)
    app.include_router(health.router)
    app.include_router(health.router, prefix=API_PREFIX, include_in_schema=False)
    return app
