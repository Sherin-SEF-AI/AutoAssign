from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.adapters.here.budget import BudgetGuard, budget_key
from app.adapters.here.client import HereClient
from app.core.circuit import CircuitBreaker
from app.core.errors import ProviderError, ProviderUnavailable
from app.core.geo import Point
from tests.helpers import fixture, no_sleep

pytestmark = pytest.mark.integration

ROUTES = "https://router.hereapi.com/v8/routes"
MATRIX = "https://matrix.router.hereapi.com/v8/matrix"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DEPART = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)  # 09:00 IST
A, B = Point(12.9352, 77.6245), Point(12.985, 77.734)


def make(clean, caps=None) -> HereClient:  # type: ignore[no-untyped-def]
    redis = clean.redis
    return HereClient(
        httpx.AsyncClient(),
        api_key="k-123",
        routing_url=ROUTES,
        matrix_url=MATRIX,
        budget=BudgetGuard(
            redis, caps or {"here_routing": 600, "here_matrix": 40, "here_matrix_elements": 2000}
        ),
        routing_breaker=CircuitBreaker(redis, "here_routing", threshold=5, open_s=600),
        matrix_breaker=CircuitBreaker(redis, "here_matrix", threshold=5, open_s=600),
        retries=3,
        backoff_base_s=0.01,
        timeout_s=5,
        sleep=no_sleep,
    )


@respx.mock
async def test_route_parses_and_sends_key_as_query(clean) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(ROUTES).mock(
        return_value=httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json"))
    )
    leg = await make(clean).route(A, B, DEPART, NOW)
    assert (
        leg.duration_s == 3761
        and leg.free_flow_s == 1934
        and leg.distance_m == 18432
        and leg.source == "here"
    )
    req = route.calls.last.request
    assert req.url.scheme == "https"
    assert req.url.params["apiKey"] == "k-123"
    assert req.url.params["departureTime"] == "2026-10-06T09:00:00+05:30"
    assert req.url.params["transportMode"] == "car" and req.url.params["return"] == "summary"
    assert "apiKey" not in req.headers and "k-123" not in str(req.headers)
    assert int(await clean.redis.get(budget_key("here_routing", NOW))) == 1


@respx.mock
async def test_route_retries_on_429_and_5xx(clean) -> None:  # type: ignore[no-untyped-def]
    respx.get(ROUTES).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(503),
            httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json")),
        ]
    )
    leg = await make(clean).route(A, B, DEPART, NOW)
    assert leg.duration_s == 3761


@respx.mock
async def test_route_4xx_is_not_retried(clean) -> None:  # type: ignore[no-untyped-def]
    r = respx.get(ROUTES).mock(return_value=httpx.Response(400, json={"title": "bad"}))
    with pytest.raises(ProviderError):
        await make(clean).route(A, B, DEPART, NOW)
    assert r.call_count == 1


@respx.mock
async def test_breaker_opens_after_five_failures(clean) -> None:  # type: ignore[no-untyped-def]
    r = respx.get(ROUTES).mock(return_value=httpx.Response(500))
    client = make(clean)
    for _ in range(5):
        with pytest.raises(ProviderError):
            await client.route(A, B, DEPART, NOW)
    assert await client.routing_breaker.state() == "open"
    calls = r.call_count
    with pytest.raises(ProviderUnavailable):
        await client.route(A, B, DEPART, NOW)
    assert r.call_count == calls


@respx.mock
async def test_budget_cap_blocks_calls(clean) -> None:  # type: ignore[no-untyped-def]
    r = respx.get(ROUTES).mock(
        return_value=httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json"))
    )
    client = make(clean, {"here_routing": 2, "here_matrix": 1, "here_matrix_elements": 4})
    await client.route(A, B, DEPART, NOW)
    await client.route(A, B, DEPART, NOW)
    with pytest.raises(ProviderUnavailable, match="budget"):
        await client.route(A, B, DEPART, NOW)
    assert r.call_count == 2
    assert int(await clean.redis.get(budget_key("here_routing", NOW))) == 2
    ttl = await clean.redis.ttl(budget_key("here_routing", NOW))
    assert 0 < ttl <= 26 * 3600


@respx.mock
async def test_matrix_sync_and_error_codes(clean) -> None:  # type: ignore[no-untyped-def]
    m = respx.post(MATRIX).mock(return_value=httpx.Response(200, json=fixture("here", "matrix_2x2.json")))
    grid = await make(clean).matrix([A, B], [B, A], DEPART, NOW)
    assert grid[0][0] is not None and grid[0][0].duration_s == 612 and grid[0][1].distance_m == 11870  # type: ignore[union-attr]
    assert grid[1][1] is None
    body = m.calls.last.request
    assert body.url.params["async"] == "false" and body.url.params["apiKey"] == "k-123"
    import json

    sent = json.loads(body.content)
    assert sent["regionDefinition"] == {
        "type": "circle",
        "center": {"lat": 12.97, "lng": 77.59},
        "radius": 60000,
    }
    assert sent["matrixAttributes"] == ["travelTimes", "distances"]
    assert int(await clean.redis.get(budget_key("here_matrix_elements", NOW))) == 4
    assert int(await clean.redis.get(budget_key("here_matrix", NOW))) == 1


@respx.mock
async def test_matrix_202_polls_status(clean) -> None:  # type: ignore[no-untyped-def]
    respx.post(MATRIX).mock(return_value=httpx.Response(202, json=fixture("here", "matrix_accepted.json")))
    status_url = fixture("here", "matrix_accepted.json")["statusUrl"]
    respx.get(status_url).mock(
        side_effect=[
            httpx.Response(200, json={"status": "inProgress"}),
            httpx.Response(200, json=fixture("here", "matrix_status_completed.json")),
        ]
    )
    result_url = fixture("here", "matrix_status_completed.json")["resultUrl"]
    respx.get(result_url).mock(return_value=httpx.Response(200, json=fixture("here", "matrix_2x2.json")))
    grid = await make(clean).matrix([A, B], [B, A], DEPART, NOW)
    assert grid[1][0].duration_s == 1530  # type: ignore[union-attr]


async def test_matrix_rejects_oversize(clean) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        await make(clean).matrix([A] * 51, [B], DEPART, NOW)


@respx.mock
async def test_matrix_elements_cap(clean) -> None:  # type: ignore[no-untyped-def]
    m = respx.post(MATRIX).mock(return_value=httpx.Response(200, json=fixture("here", "matrix_2x2.json")))
    client = make(clean, {"here_routing": 1, "here_matrix": 10, "here_matrix_elements": 6})
    await client.matrix([A, B], [B, A], DEPART, NOW)
    with pytest.raises(ProviderUnavailable):
        await client.matrix([A, B], [B, A], DEPART, NOW)
    assert m.call_count == 1
    # A rejected reservation leaves the counters untouched.
    assert int(await clean.redis.get(budget_key("here_matrix", NOW))) == 1
