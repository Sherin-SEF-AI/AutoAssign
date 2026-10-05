"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPAuthorizationCredentials, HTTPBearer
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


bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")
# EventSource and map tile requests cannot set headers, so the token may come as a query parameter.
token_query_scheme = APIKeyQuery(name="token", auto_error=False, description="JWT for SSE and map tiles")
service_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, description="Service to service key")


async def current_user(
    ctx: Ctx,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    token: Annotated[str | None, Depends(token_query_scheme)],
) -> Principal:
    if credentials is not None and credentials.credentials:
        raw = credentials.credentials
    elif token:
        raw = token
    else:
        raise Unauthorized("missing bearer token", code="missing_token")
    return decode_token(ctx.base_settings, raw)


User = Annotated[Principal, Depends(current_user)]


async def require_admin(user: User) -> Principal:
    if not user.is_admin:
        raise Forbidden("admin role required", code="admin_required")
    return user


Admin = Annotated[Principal, Depends(require_admin)]


async def service_key(ctx: Ctx, x_api_key: Annotated[str | None, Depends(service_key_scheme)]) -> Principal:
    return check_service_key(ctx.base_settings, x_api_key)


Service = Annotated[Principal, Depends(service_key)]
