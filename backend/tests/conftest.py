"""Shared fixtures. Integration tests need Postgres and Redis (see .env.test)."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_file(ROOT / ".env.test")
os.environ.setdefault("ENVIRONMENT", "test")
# Never touch a real HERE account from tests: HERE calls are replayed through respx fixtures.
os.environ["HERE_API_KEY"] = "test-here-key"
os.environ.pop("OSRM_URL", None)


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    from app.config import Settings

    return Settings()  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def migrated_db(settings) -> Iterator[None]:  # type: ignore[no-untyped-def]
    env = {**os.environ, "DATABASE_URL": settings.database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    yield


@pytest.fixture(scope="session")
async def ctx(settings, migrated_db) -> AsyncIterator[object]:  # type: ignore[no-untyped-def]
    from app.core.clock import WallClock
    from app.runtime import close_context, create_context

    context = await create_context(settings, clock=WallClock(), pool_size=5)
    await context.redis.flushdb()
    yield context
    await close_context(context)


@pytest.fixture
async def clean(ctx) -> AsyncIterator[object]:  # type: ignore[no-untyped-def]
    """Empty operational tables and Redis before a test."""
    from app.db.maintenance import reset_dataset
    from app.db.session import session_scope

    async with session_scope(ctx.factory) as session:
        await reset_dataset(session)
    await ctx.redis.flushdb()
    ctx.settings_cache.invalidate()
    yield ctx


@pytest.fixture
async def app_client(clean):  # type: ignore[no-untyped-def]
    import httpx

    from app.main import create_app

    app = create_app(clean.base_settings, ctx=clean)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


@pytest.fixture
async def admin_headers(app_client, settings) -> dict[str, str]:  # type: ignore[no-untyped-def]
    resp = await app_client.post(
        "/api/v1/auth/login", json={"email": settings.admin_email, "password": settings.admin_password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def ops_headers(app_client, clean) -> dict[str, str]:  # type: ignore[no-untyped-def]
    from app.api.security import hash_password
    from app.db.models import AppUser
    from app.db.session import session_scope

    async with session_scope(clean.factory) as session:
        session.add(
            AppUser(email="ops@blurabbit.test", password_hash=hash_password("ops-password-1"), role="ops")
        )
    resp = await app_client.post(
        "/api/v1/auth/login", json={"email": "ops@blurabbit.test", "password": "ops-password-1"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
