from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.adapters.synthetic.writer import generate_dataset
from app.db.models import EtaLog, FactorRow, LegEstimate
from app.db.session import session_scope
from app.jobs.purge import purge_here_cache
from app.jobs.seed import after_dataset_load

pytestmark = pytest.mark.integration


def _est(source: str, created: datetime, i: int) -> LegEstimate:
    return LegEstimate(
        kind="trip",
        origin_h3=f"o{i}",
        dest_h3=f"d{i}",
        origin_lat=12.9,
        origin_lng=77.6,
        dest_lat=12.95,
        dest_lng=77.65,
        depart_bin=36,
        service_date=date(2026, 8, 1),
        source=source,
        p50_s=600,
        p80_s=700,
        p90_s=800,
        distance_m=5000,
        provider_payload={"duration": 600},
        created_at=created,
        expires_at=created + timedelta(days=30) if source == "here" else None,
    )


def _log(source: str, computed: datetime) -> EtaLog:
    return EtaLog(
        trip_id=uuid.uuid4(),
        service_date=computed.date(),
        leg_kind="trip",
        source=source,
        predicted_p50_s=600,
        predicted_p80_s=700,
        base_s=600,
        actual_s=650,
        residual_ratio=650 / 600,
        covered_p80=True,
        depart_bin=36,
        time_bin=2,
        weekday_type="weekday",
        zone_cluster=None,
        origin_lat=12.9,
        origin_lng=77.6,
        computed_at=computed,
    )


async def test_purge_removes_here_rows_past_30_days(clean) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    old, fresh = now - timedelta(days=31), now - timedelta(days=2)
    async with session_scope(clean.factory) as s:
        s.add_all(
            [_est("here", old, 1), _est("here", fresh, 2), _est("fallback", old, 3), _est("osrm", old, 4)]
        )
        s.add_all([_log("here", old), _log("here", fresh), _log("fallback", old)])
    stats = await purge_here_cache(clean, now=now)
    assert stats["leg_estimates_deleted"] == 1 and stats["eta_log_predictions_nulled"] == 1
    async with clean.factory() as s:
        sources = sorted((await s.execute(select(LegEstimate.source))).scalars())
        assert sources == ["fallback", "here", "osrm"]
        logs = (await s.execute(select(EtaLog).order_by(EtaLog.computed_at))).scalars().all()
    purged = [r for r in logs if r.source == "here" and r.predicted_p50_s is None]
    assert len(purged) == 1
    assert purged[0].actual_s == 650 and purged[0].residual_ratio == pytest.approx(650 / 600)
    assert next(r for r in logs if r.source == "fallback").predicted_p50_s == 600


async def test_backfill_and_calibration_on_synthetic_history(clean, settings) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=10, days_future=1)
    result = await after_dataset_load(clean)
    assert result["eta_actuals"]["written"] > 700
    cal = result["calibration"]
    assert cal["clusters"] == 8 and cal["factor_rows"] > 10
    async with clean.factory() as s:
        n = (await s.execute(select(func.count()).select_from(FactorRow))).scalar_one()
        assert n == cal["factor_rows"]
        glob = (await s.execute(select(FactorRow).where(FactorRow.level == "global"))).scalar_one()
    # Synthetic actuals: lognormal(0, 0.25) noise plus handling, some rain days.
    assert 0.95 < glob.r50 < 1.25 and glob.r80 > glob.r50 and glob.r90 > glob.r80
    again = await after_dataset_load(clean)
    assert again["eta_actuals"]["written"] == 0  # idempotent


async def test_budget_factors_settings_estimates_api(
    app_client, admin_headers, ops_headers, clean, settings
) -> None:  # type: ignore[no-untyped-def]
    budget = (await app_client.get("/api/v1/admin/budget", headers=ops_headers)).json()
    caps = {p["provider"]: p["cap"] for p in budget["providers"]}
    assert caps == {"here_routing": 600, "here_matrix": 40, "here_matrix_elements": 2000}
    factors = (await app_client.get("/api/v1/settings/factors", headers=ops_headers)).json()
    assert (
        factors["calibrated"] is False
        and factors["buffer_policy"] == "fixed"
        and len(factors["defaults"]) == 18
    )
    cur = (await app_client.get("/api/v1/settings", headers=ops_headers)).json()
    assert cur["values"]["buffer_fixed_s"] == 900
    denied = await app_client.put("/api/v1/settings", json={"buffer_fixed_s": 600}, headers=ops_headers)
    assert denied.status_code == 403
    bad = await app_client.put("/api/v1/settings", json={"buffer_pct": 3}, headers=admin_headers)
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_settings"
    assert bad.json()["errors"][0]["field"] == "buffer_pct"
    unknown = await app_client.put("/api/v1/settings", json={"database_url": "x"}, headers=admin_headers)
    assert unknown.status_code == 422
    ok = await app_client.put(
        "/api/v1/settings", json={"buffer_fixed_s": 600, "here_routing_daily_cap": 100}, headers=admin_headers
    )
    assert ok.status_code == 200 and ok.json()["values"]["buffer_fixed_s"] == 600
    budget = (await app_client.get("/api/v1/admin/budget", headers=ops_headers)).json()
    assert {p["provider"]: p["cap"] for p in budget["providers"]}["here_routing"] == 100
    audit = (await app_client.get("/api/v1/settings/audit", headers=ops_headers)).json()
    assert audit[0]["changes"] == {"buffer_fixed_s": 600, "here_routing_daily_cap": 100}
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=0, days_future=1)
    tomorrow = (today + timedelta(days=1)).isoformat()
    run = await app_client.post(
        "/api/v1/jobs/estimate_day/run", json={"service_date": tomorrow}, headers=ops_headers
    )
    assert run.status_code == 202
    import asyncio

    for _ in range(300):
        r = (await app_client.get(f"/api/v1/jobs/runs/{run.json()['run_id']}", headers=ops_headers)).json()
        if r["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.1)
    assert r["status"] == "succeeded", r
    est = (
        await app_client.get("/api/v1/admin/estimates", params={"date": tomorrow}, headers=ops_headers)
    ).json()
    kinds = {(row["kind"], row["source"]) for row in est["rows"]}
    assert (
        ("trip", "fallback") in kinds and ("deadhead", "fallback") in kinds and ("hub", "fallback") in kinds
    )
    assert est["last_run"]["job_name"] == "estimate_day"
