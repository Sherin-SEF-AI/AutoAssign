"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import Principal, check_service_key, decode_token
from app.config import Settings
from app.core.errors import Forbidden, Unauthorized
from app.jobs.context import AppContext


def get_ctx(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


async def get_session(ctx: Ctx) -> AsyncIterator[AsyncSession]:
    async with ctx.factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


Session = Annotated[AsyncSession, Depends(get_session)]


async def get_settings_dep(ctx: Ctx, session: Session) -> Settings:
    return await ctx.settings_cache.get(session)


EffectiveSettings = Annotated[Settings, Depends(get_settings_dep)]


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token", code="missing_token")
    return authorization.split(" ", 1)[1].strip()


async def current_user(
    ctx: Ctx, authorization: Annotated[str | None, Header()] = None, token: str | None = None
) -> Principal:
    # The token query parameter exists for EventSource and map tile requests, which cannot set headers.
    raw = token if token and not authorization else _bearer(authorization)
    principal = decode_token(ctx.base_settings, raw)
    return principal


User = Annotated[Principal, Depends(current_user)]


async def require_admin(user: User) -> Principal:
    if not user.is_admin:
        raise Forbidden("admin role required", code="admin_required")
    return user


Admin = Annotated[Principal, Depends(require_admin)]


async def service_key(ctx: Ctx, x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    return check_service_key(ctx.base_settings, x_api_key)


Service = Annotated[Principal, Depends(service_key)]
