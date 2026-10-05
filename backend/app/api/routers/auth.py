"""POST /auth/login."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.deps import Ctx, Session, User
from app.api.security import authenticate, issue_token, login_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    role: Literal["ops", "admin"]
    email: str
    expires_at: datetime


class MeOut(BaseModel):
    email: str
    role: Literal["ops", "admin"]


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, ctx: Ctx, session: Session) -> LoginOut:
    ip = request.client.host if request.client else "unknown"
    await login_rate_limit(ctx.redis, ip, ctx.base_settings.login_rate_per_minute)
    user = await authenticate(session, body.email, body.password)
    token, expires = issue_token(ctx.base_settings, user)
    return LoginOut(access_token=token, role=user.role, email=user.email, expires_at=expires)


@router.get("/me", response_model=MeOut)
async def me(user: User) -> MeOut:
    return MeOut(email=user.subject, role=user.role)
