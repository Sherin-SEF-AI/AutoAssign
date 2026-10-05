"""Server-sent events stream backed by the Redis event bus."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import Ctx, User
from app.core.events import subscribe

router = APIRouter(prefix="/events", tags=["events"])
KEEPALIVE_S = 15.0


def format_sse(message: dict[str, Any]) -> str:
    return f"event: {message['event']}\ndata: {json.dumps(message, default=str)}\n\n"


@router.get("/stream")
async def stream(request: Request, ctx: Ctx, _: User) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

        async def pump() -> None:
            async for message in subscribe(ctx.redis):
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(message)

        task = asyncio.create_task(pump())
        try:
            while not await request.is_disconnected():
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield format_sse(message)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
