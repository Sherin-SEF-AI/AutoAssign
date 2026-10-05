"""HttpSource against the recorded fixture server, and the identical plan acceptance check."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters.http.source import HttpSource
from app.adapters.synthetic.source import SyntheticSource
from app.adapters.synthetic.writer import generate_dataset
from app.core.errors import ProviderError
from app.db.maintenance import reset_dataset
from app.db.session import session_scope
from app.eta.factory import build_estimator
from app.jobs.context import AppContext
from app.jobs.ingest import ingest_date
from app.plan.inputs import load_inputs
from app.plan.service import solve_inputs
from app.tools.fixture_server import create_fixture_app, record

pytestmark = pytest.mark.integration
KEY = "fixture-key"


def http_settings(settings):  # type: ignore[no-untyped-def]
    return settings.model_copy(
        update={
            "data_source": "http",
            "upstream_userapp_url": "http://userapp.test",
            "upstream_driverapp_url": "http://driverapp.test",
            "upstream_admin_url": "http://admin.test",
            "upstream_api_key": KEY,
            "http_retries": 1,
            "http_backoff_base_s": 0.0,
        }
    )


async def test_http_source_pages_and_validates(settings, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    today = datetime.now(UTC).date()
    day = today + timedelta(days=1)
    synthetic = SyntheticSource(42, lambda: today)
    counts = await record(synthetic, tmp_path, [day])
    assert counts[f"userapp/trips/{day}.json"] > 80
    app = create_fixture_app(tmp_path, KEY, page_size=17)
    src = HttpSource(http_settings(settings), transport=httpx.ASGITransport(app=app))
    trips = await src.list_trips(day)
    assert [t.model_dump() for t in trips] == [t.model_dump() for t in await synthetic.list_trips(day)]
    assert len(await src.list_drivers(day)) == 30
    assert len(await src.list_vehicles()) == 30 and len(await src.list_hubs()) == 2
    assert len(await src.list_soc_checkins(day)) > 20
    assert [p async for p in src.stream_pings(datetime.now(UTC))] == []
    assert [e async for e in src.trip_events(datetime.now(UTC))] == []
    await src.aclose()
    wrong = HttpSource(
        http_settings(settings).model_copy(update={"upstream_api_key": "bad"}),
        transport=httpx.ASGITransport(app=app),
    )
    with pytest.raises(ProviderError):
        await wrong.list_hubs()
    await wrong.aclose()


@respx.mock
async def test_http_source_rejects_contract_violations_and_retries(settings) -> None:  # type: ignore[no-untyped-def]
    respx.get("http://admin.test/v1/hubs").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"items": [{"hub_id": "nope"}]})]
    )
    src = HttpSource(http_settings(settings))
    with pytest.raises(ProviderError, match="violates contract"):
        await src.list_hubs()
    respx.get("http://admin.test/v1/vehicles").mock(return_value=httpx.Response(200, json={"rows": []}))
    with pytest.raises(ProviderError, match="missing items"):
        await src.list_vehicles()
    await src.aclose()


async def _solve(ctx: AppContext, day) -> dict:  # type: ignore[no-untyped-def]
    settings = await ctx.settings()
    estimator = await build_estimator(settings, ctx.factory, ctx.redis, ctx.http)
    async with session_scope(ctx.factory) as session:
        inputs = await load_inputs(session, day)
        plan = await solve_inputs(
            session, settings, estimator, inputs, created_by="test", trigger="test", reason=""
        )
        from app.db.queries import plan_assignments

        rows = await plan_assignments(session, plan.plan_id)
    return {r.trip_id: (r.driver_id, r.seq, r.unassigned_reason, r.planned_depart_at) for r in rows}


async def test_switching_to_http_gives_identical_plan(clean, settings, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    day = today + timedelta(days=1)
    clean.base_settings = clean.base_settings.model_copy(update={"solver_solution_limit": 25})
    clean.settings_cache.base = clean.base_settings
    clean.settings_cache.invalidate()
    try:
        await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=0, days_future=1)
        synthetic_plan = await _solve(clean, day)
        await record(SyntheticSource(42, lambda: today), tmp_path, [day])
        async with session_scope(clean.factory) as session:
            await reset_dataset(session)
        await clean.redis.flushdb()
        http_src = HttpSource(
            http_settings(settings), transport=httpx.ASGITransport(app=create_fixture_app(tmp_path, KEY))
        )
        http_ctx = AppContext(
            base_settings=clean.base_settings,
            engine=clean.engine,
            factory=clean.factory,
            redis=clean.redis,
            source=http_src,
            clock=clean.clock,
            notifier=clean.notifier,
            http=clean.http,
        )
        stats = await ingest_date(http_ctx, day)
        assert stats["created"]
        http_plan = await _solve(http_ctx, day)
        await http_src.aclose()
    finally:
        clean.base_settings = settings
        clean.settings_cache.base = settings
        clean.settings_cache.invalidate()
    assert http_plan == synthetic_plan
