from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import date

import httpx
import pytest
import respx

from app.adapters.base import Notification
from app.adapters.notify.notifiers import (
    CompositeNotifier,
    LogNotifier,
    SseNotifier,
    WebhookNotifier,
    sign_payload,
)
from app.api.routers.events import format_sse
from app.core.events import subscribe

pytestmark = pytest.mark.integration


async def test_sse_notifier_publishes_without_driver_payload(clean) -> None:  # type: ignore[no-untyped-def]
    got: list[dict] = []

    async def listen() -> None:
        async for msg in subscribe(clean.redis):
            got.append(msg)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    plan_id = uuid.uuid4()
    await SseNotifier(clean.redis).send(
        Notification(
            event="plan.published",
            service_date=date(2026, 10, 6),
            plan_id=plan_id,
            data={"version": 3, "drivers": [{"driver_id": "x"}]},
        )
    )
    await asyncio.wait_for(task, 5)
    assert got[0]["event"] == "plan.published"
    assert got[0]["data"]["plan_id"] == str(plan_id) and "drivers" not in got[0]["data"]
    frame = format_sse(got[0])
    assert frame.startswith("event: plan.published\ndata: ") and frame.endswith("\n\n")


@respx.mock
async def test_webhook_is_signed_and_failures_do_not_propagate() -> None:
    hook = respx.post("https://bridge.test/hook").mock(return_value=httpx.Response(200))
    n = WebhookNotifier(httpx.AsyncClient(), "https://bridge.test/hook", "s3cret", retries=0, backoff_s=0)
    note = Notification(event="plan.published", plan_id=uuid.uuid4(), data={"drivers": []})
    await n.send(note)
    req = hook.calls.last.request
    expected = "sha256=" + hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Dispatch-Signature"] == expected == sign_payload("s3cret", req.content)
    assert json.loads(req.content)["event"] == "plan.published"
    respx.post("https://bridge.test/hook").mock(return_value=httpx.Response(500))
    composite = CompositeNotifier([LogNotifier(), n])
    await composite.send(note)  # logged, not raised


async def test_stream_endpoint_emits_events(clean, settings) -> None:  # type: ignore[no-untyped-def]
    """Real HTTP server: the ASGI test transport buffers responses and cannot test streaming."""
    import socket

    import uvicorn

    from app.api.security import issue_token
    from app.core.events import publish
    from app.db.models import AppUser
    from app.main import create_app

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app = create_app(settings, ctx=clean)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    serve = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    token, _ = issue_token(settings, AppUser(email="ops@x.y", password_hash="h", role="ops"))
    lines: list[str] = []

    async def read() -> None:
        async with (
            httpx.AsyncClient() as client,
            client.stream(
                "GET", f"http://127.0.0.1:{port}/api/v1/events/stream", params={"token": token}
            ) as resp,
        ):
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                lines.append(line)
                if line.startswith("data:") and "plan.repaired" in line:
                    return

    task = asyncio.create_task(read())
    try:
        for _ in range(50):
            await asyncio.sleep(0.1)
            await publish(clean.redis, "plan.repaired", {"plan_id": "p1"})
            if task.done():
                break
        await asyncio.wait_for(task, 5)
    finally:
        server.should_exit = True
        await serve
    assert "event: plan.repaired" in lines
