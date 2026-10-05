"""Shared dependencies for jobs and API handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.adapters.base import DataSource, Notifier
from app.config import Settings
from app.core.clock import Clock
from app.core.timeutil import service_date_of
from app.db.session import SessionFactory, session_scope
from app.db.settings_store import SettingsCache


@dataclass(slots=True)
class AppContext:
    base_settings: Settings
    engine: AsyncEngine
    factory: SessionFactory
    redis: Redis
    source: DataSource
    clock: Clock
    notifier: Notifier
    http: httpx.AsyncClient
    settings_cache: SettingsCache = field(init=False)

    def __post_init__(self) -> None:
        self.settings_cache = SettingsCache(self.base_settings)

    async def settings(self) -> Settings:
        async with session_scope(self.factory) as session:
            return await self.settings_cache.get(session)

    async def today(self) -> date:
        return service_date_of(await self.clock.now())
