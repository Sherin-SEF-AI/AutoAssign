from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from app.adapters.here.budget import BudgetGuard
from app.adapters.here.client import HereClient
from app.adapters.osrm.client import OsrmClient
from app.core.circuit import CircuitBreaker
from app.core.geo import Point
from app.core.metrics import ETA_FALLBACK
from app.db.models import LegEstimate
from app.eta.estimator import LegEstimator, _matrix_blocks
from app.eta.factors import FactorTable, default_factors
from app.eta.types import LegRequest, cache_key
from tests.helpers import fixture, no_sleep

pytestmark = pytest.mark.integration

ROUTES = "https://router.hereapi.com/v8/routes"
MATRIX = "https://matrix.router.hereapi.com/v8/matrix"
OSRM = "http://osrm.test"
DEPART = datetime(2026, 10, 6, 3, 30, tzinfo=UTC)  # 09:00 IST, Tuesday
A, B, C = Point(12.9352, 77.6245), Point(12.985, 77.734), Point(12.9116, 77.6389)


def estimator(clean, *, here=True, osrm=False, caps=None, settings_update=None) -> LegEstimator:  # type: ignore[no-untyped-def]
    s = clean.base_settings.model_copy(
        update={
            "here_api_key": "k",
            "here_enabled": here,
            "osrm_url": OSRM if osrm else None,
            **(settings_update or {}),
        }
    )
    redis = clean.redis
    hc = HereClient(
        httpx.AsyncClient(),
        api_key="k",
        routing_url=ROUTES,
        matrix_url=MATRIX,
        budget=BudgetGuard(
            redis, caps or {"here_routing": 600, "here_matrix": 40, "here_matrix_elements": 2000}
        ),
        routing_breaker=CircuitBreaker(redis, "here_routing", threshold=5, open_s=600),
        matrix_breaker=CircuitBreaker(redis, "here_matrix", threshold=5, open_s=600),
        retries=0,
        backoff_base_s=0.0,
        timeout_s=5,
        sleep=no_sleep,
    )
    oc = OsrmClient(
        httpx.AsyncClient(),
        base_url=OSRM,
        breaker=CircuitBreaker(redis, "osrm", threshold=5, open_s=600),
        retries=0,
        backoff_base_s=0,
        timeout_s=5,
    )
    return LegEstimator(
        settings=s,
        factory=clean.factory,
        redis=redis,
        factors=FactorTable(defaults=default_factors(s)),
        here=hc if here else None,
        osrm=oc if osrm else None,
    )


@respx.mock
async def test_chain_here_then_cache(clean) -> None:  # type: ignore[no-untyped-def]
    route = respx.get(ROUTES).mock(
        return_value=httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json"))
    )
    est = estimator(clean)
    first = await est.estimate(A, B, DEPART, "trip")
    assert first.source == "here" and not first.cached
    # HERE duration is p50 base (plus handling for trip legs), p80 and p90 from default factors.
    base = 3761 + clean.base_settings.handling_s
    assert first.p50_s == pytest.approx(base) and first.p80_s == pytest.approx(base * 1.15)
    assert first.p90_s == pytest.approx(base * 1.30)
    again = await estimator(clean).estimate(A, B, DEPART + timedelta(minutes=5), "trip")
    assert again.cached and again.source == "here" and route.call_count == 1
    await clean.redis.flushdb()  # hot cache gone, DB row still serves
    third = await estimator(clean).estimate(A, B, DEPART, "trip")
    assert third.cached and route.call_count == 1
    async with clean.factory() as s:
        row = (await s.execute(select(LegEstimate))).scalar_one()
    assert row.source == "here" and row.expires_at == row.created_at + timedelta(days=30)
    assert row.provider_payload["baseDuration"] == 1934


@respx.mock
async def test_budget_exhausted_degrades_to_osrm_then_fallback(clean) -> None:  # type: ignore[no-untyped-def]
    respx.get(ROUTES).mock(
        return_value=httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json"))
    )
    osrm = respx.get(url__startswith=f"{OSRM}/route/v1/driving/").mock(
        return_value=httpx.Response(
            200, json={"code": "Ok", "routes": [{"duration": 1500.0, "distance": 17000.0}]}
        )
    )
    est = estimator(clean, osrm=True, caps={"here_routing": 1, "here_matrix": 0, "here_matrix_elements": 0})
    one = await est.estimate(A, B, DEPART, "trip")
    two = await est.estimate(A, C, DEPART, "trip")
    assert one.source == "here" and two.source == "osrm" and osrm.call_count == 1
    # OSRM free-flow scaled by the congestion default for 07:00 to 10:00 (38/15).
    assert two.p50_s == pytest.approx((1500 + 240) * 38 / 15, rel=1e-3)
    respx.get(url__startswith=f"{OSRM}/route/v1/driving/").mock(return_value=httpx.Response(500))
    before = ETA_FALLBACK._value.get()
    three = await est.estimate(B, C, DEPART, "trip")
    assert three.source == "fallback"
    assert ETA_FALLBACK._value.get() == before + 1


async def test_fallback_always_completes(clean) -> None:  # type: ignore[no-untyped-def]
    est = estimator(clean, here=False)
    reqs = [LegRequest(A, B, DEPART), LegRequest(B, C, DEPART), LegRequest(A, A, DEPART)]
    out = await est.estimate_many(reqs, "deadhead")
    assert [o.source for o in out] == ["fallback"] * 3
    assert out[2].p50_s == 0
    # 15 km/h in the morning peak: distance / speed.
    assert out[0].p50_s == pytest.approx(out[0].distance_m / 1000 / 15 * 3600, rel=1e-6)
    assert est.stats.by_source["fallback"] == 3


@respx.mock
async def test_matrix_batches_deadheads_within_caps(clean) -> None:  # type: ignore[no-untyped-def]
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        calls.append((len(body["origins"]), len(body["destinations"])))
        n_o, n_d = len(body["origins"]), len(body["destinations"])
        return httpx.Response(
            200,
            json={
                "matrixId": "m",
                "matrix": {
                    "numOrigins": n_o,
                    "numDestinations": n_d,
                    "travelTimes": [600] * (n_o * n_d),
                    "distances": [5000] * (n_o * n_d),
                },
            },
        )

    respx.post(MATRIX).mock(side_effect=respond)
    est = estimator(clean, caps={"here_routing": 0, "here_matrix": 3, "here_matrix_elements": 4000})
    origins = [Point(12.60 + i * 0.01, 77.60) for i in range(60)]
    dests = [Point(12.95, 77.30 + j * 0.01) for j in range(55)]
    reqs = [LegRequest(o, d, DEPART) for o in origins for d in dests[:3]]
    reqs += [LegRequest(origins[0], d, DEPART + timedelta(hours=2)) for d in dests]
    out = await est.estimate_many(reqs, "deadhead")
    assert all(n_o <= 50 and n_d <= 50 for n_o, n_d in calls)
    assert len(calls) == 3  # matrix cap reached, rest falls through
    sources = {o.source for o in out}
    assert sources == {"here", "fallback"}
    assert est.stats.here_matrix_calls == 3


def test_matrix_blocks_only_wanted_pairs() -> None:
    reqs = {
        cache_key("deadhead", LegRequest(A, B, DEPART)): LegRequest(A, B, DEPART),
        cache_key("deadhead", LegRequest(C, B, DEPART)): LegRequest(C, B, DEPART),
    }
    groups = list(_matrix_blocks(reqs, 50))
    assert len(groups) == 1
    _, blocks = groups[0]
    assert len(blocks) == 1 and len(blocks[0][0]) == 2 and len(blocks[0][1]) == 1


def test_cache_key_bins_and_cells() -> None:
    k1 = cache_key("trip", LegRequest(A, B, DEPART))
    k2 = cache_key("trip", LegRequest(Point(A.lat + 0.00001, A.lng), B, DEPART + timedelta(minutes=14)))
    k3 = cache_key("trip", LegRequest(A, B, DEPART + timedelta(minutes=15)))
    assert k1 == k2 and k1 != k3
    assert k1.service_date == date(2026, 10, 6) and k1.depart_bin == 36


async def test_fallback_rows_never_override_provider_rows(clean) -> None:  # type: ignore[no-untyped-def]
    with respx.mock:
        respx.get(ROUTES).mock(
            return_value=httpx.Response(200, json=fixture("here", "route_koramangala_whitefield.json"))
        )
        await estimator(clean).estimate(A, B, DEPART, "trip")
    await clean.redis.flushdb()
    est = estimator(clean, here=False)
    got = await est.estimate(A, B, DEPART, "trip")
    assert got.source == "here"
    async with clean.factory() as s:
        assert (await s.execute(select(func.count()).select_from(LegEstimate))).scalar_one() == 1
