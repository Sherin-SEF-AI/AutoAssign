"""Selects the configured DataSource and Notifiers. The only place that names implementations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
from redis.asyncio import Redis

from app.adapters.base import DataSource, Notifier
from app.adapters.notify.notifiers import CompositeNotifier, LogNotifier, SseNotifier, WebhookNotifier
from app.config import Settings


def build_datasource(settings: Settings, today: Callable[[], date], redis: Redis | None = None) -> DataSource:
    if settings.data_source == "http":
        from app.adapters.http.source import HttpSource

        return HttpSource(settings)
    from app.adapters.synthetic.source import SyntheticSource

    return SyntheticSource(settings.synthetic_seed, today, redis)


def build_notifier(settings: Settings, redis: Redis, client: httpx.AsyncClient) -> CompositeNotifier:
    notifiers: list[Notifier] = [LogNotifier(), SseNotifier(redis)]
    if settings.notify_webhook_url and settings.notify_webhook_secret:
        notifiers.append(
            WebhookNotifier(
                client,
                settings.notify_webhook_url,
                settings.notify_webhook_secret,
                retries=settings.http_retries,
                backoff_s=settings.http_backoff_base_s,
            )
        )
    return CompositeNotifier(notifiers)
