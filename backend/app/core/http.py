"""HTTP call helper with retries, exponential backoff with jitter and typed failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.core.metrics import PROVIDER_ERRORS

log = get_logger("http")

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def backoff_delay(attempt: int, base_s: float, cap_s: float = 8.0) -> float:
    """Full jitter: uniform(0, min(cap, base * 2^attempt))."""
    return random.uniform(0, min(cap_s, base_s * (2**attempt)))


async def call_with_retries(
    provider: str,
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int,
    backoff_base_s: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    """Run send() with retries on 429, 5xx and transport errors. Raises ProviderError."""
    last_error = "unknown"
    for attempt in range(retries + 1):
        try:
            response = await send()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            PROVIDER_ERRORS.labels(provider=provider, kind="transport").inc()
        else:
            if response.status_code < 400:
                return response
            if response.status_code not in RETRY_STATUSES:
                PROVIDER_ERRORS.labels(provider=provider, kind=f"http_{response.status_code}").inc()
                raise ProviderError(
                    provider,
                    f"HTTP {response.status_code}: {response.text[:300]}",
                    retryable=False,
                )
            last_error = f"HTTP {response.status_code}"
            PROVIDER_ERRORS.labels(provider=provider, kind=f"http_{response.status_code}").inc()
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.isdigit() and attempt < retries:
                await sleep(min(float(retry_after), 30.0))
                continue
        if attempt < retries:
            delay = backoff_delay(attempt, backoff_base_s)
            log.info(
                "http_retry",
                provider=provider,
                attempt=attempt + 1,
                delay_s=round(delay, 3),
                error=last_error,
            )
            await sleep(delay)
    raise ProviderError(provider, f"failed after {retries + 1} attempts: {last_error}")
