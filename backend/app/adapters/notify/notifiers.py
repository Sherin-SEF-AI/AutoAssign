"""Notifier implementations: log (always on), SSE (web), signed webhook (driver app bridge)."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence

import httpx
from redis.asyncio import Redis

from app.adapters.base import Notification, Notifier
from app.core.errors import ProviderError
from app.core.events import publish
from app.core.http import call_with_retries
from app.core.logging import get_logger

log = get_logger("notify")


class LogNotifier:
    name = "log"

    async def send(self, notification: Notification) -> None:
        log.info(
            "notification",
            notify_event=notification.event,
            service_date=str(notification.service_date) if notification.service_date else None,
            plan_id=str(notification.plan_id) if notification.plan_id else None,
            keys=sorted(notification.data.keys()),
        )


class SseNotifier:
    """Publishes to the Redis channel behind GET /events/stream. Driver payloads are not broadcast."""

    name = "sse"

    def __init__(self, redis: Redis):
        self.redis = redis

    async def send(self, notification: Notification) -> None:
        data = {k: v for k, v in notification.data.items() if k != "drivers"}
        data["service_date"] = str(notification.service_date) if notification.service_date else None
        data["plan_id"] = str(notification.plan_id) if notification.plan_id else None
        await publish(self.redis, notification.event, data)


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookNotifier:
    name = "webhook"

    def __init__(self, client: httpx.AsyncClient, url: str, secret: str, *, retries: int, backoff_s: float):
        self.client = client
        self.url = url
        self.secret = secret
        self.retries = retries
        self.backoff_s = backoff_s

    async def send(self, notification: Notification) -> None:
        body = json.dumps(
            notification.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "X-Dispatch-Event": notification.event,
            "X-Dispatch-Signature": sign_payload(self.secret, body),
        }
        await call_with_retries(
            "webhook",
            lambda: self.client.post(self.url, content=body, headers=headers),
            retries=self.retries,
            backoff_base_s=self.backoff_s,
        )


class CompositeNotifier:
    """Fans out to every notifier. One failing channel never blocks the others."""

    name = "composite"

    def __init__(self, notifiers: Sequence[Notifier]):
        self.notifiers = list(notifiers)

    async def send(self, notification: Notification) -> None:
        for n in self.notifiers:
            try:
                await n.send(notification)
            except ProviderError as exc:
                log.error("notifier_failed", notifier=n.name, notify_event=notification.event, error=str(exc))
            except Exception as exc:  # a broken channel must not fail the job that notifies
                log.exception(
                    "notifier_crashed", notifier=n.name, notify_event=notification.event, error=str(exc)
                )
