"""Alembic environment using the async engine and DATABASE_URL from the environment."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    return url


def include_object(obj: object, name: str | None, type_: str, reflected: bool, compare_to: object) -> bool:
    # Ping partitions are managed at runtime, not by the ORM metadata.
    if type_ == "table" and name is not None and name.startswith("ping_") and reflected:
        return False
    return not (type_ == "index" and name is not None and name.startswith("ping_") and reflected)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
