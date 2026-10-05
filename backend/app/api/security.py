"""Password hashing, JWT issuing and verification, login rate limiting."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.errors import TooManyRequests, Unauthorized
from app.db.models import AppUser

ROLES = ("ops", "admin")
ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: str
    kind: str  # user or service

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def issue_token(settings: Settings, user: AppUser, now: datetime | None = None) -> tuple[str, datetime]:
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(hours=settings.jwt_ttl_hours)
    payload = {
        "sub": user.email,
        "uid": str(user.user_id),
        "role": user.role,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires


def decode_token(settings: Settings, token: str) -> Principal:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("token expired", code="token_expired") from exc
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid token", code="invalid_token") from exc
    role = payload.get("role")
    if role not in ROLES:
        raise Unauthorized("invalid role claim", code="invalid_token")
    return Principal(subject=str(payload["sub"]), role=str(role), kind="user")


def check_service_key(settings: Settings, key: str | None) -> Principal:
    if not key:
        raise Unauthorized("missing X-API-Key", code="missing_api_key")
    for candidate in settings.service_api_keys:
        if hmac.compare_digest(candidate.encode(), key.encode()):
            return Principal(subject="service", role="service", kind="service")
    raise Unauthorized("invalid X-API-Key", code="invalid_api_key")


async def login_rate_limit(redis: Redis, ip: str, per_minute: int, now: datetime | None = None) -> None:
    stamp = now or datetime.now(UTC)
    key = f"ratelimit:login:{ip}:{stamp.strftime('%Y%m%d%H%M')}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 70)
    if count > per_minute:
        raise TooManyRequests("too many login attempts, retry in a minute", code="login_rate_limited")


async def authenticate(session: AsyncSession, email: str, password: str) -> AppUser:
    user = (await session.execute(select(AppUser).where(AppUser.email == email.lower()))).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("invalid email or password", code="invalid_credentials")
    return user


async def ensure_admin(session: AsyncSession, settings: Settings) -> bool:
    email = settings.admin_email.lower()
    existing = (await session.execute(select(AppUser).where(AppUser.email == email))).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(AppUser(email=email, password_hash=hash_password(settings.admin_password), role="admin"))
    return True
