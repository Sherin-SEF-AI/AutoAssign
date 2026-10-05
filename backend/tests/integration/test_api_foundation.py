from __future__ import annotations

from datetime import timedelta

import pytest

from app.adapters.synthetic.writer import generate_dataset

pytestmark = pytest.mark.integration


async def test_health_ready_metrics(app_client) -> None:  # type: ignore[no-untyped-def]
    assert (await app_client.get("/healthz")).json() == {"status": "ok"}
    ready = (await app_client.get("/readyz")).json()
    assert ready["status"] == "ok"
    assert ready["checks"]["database"] == "ok" and ready["checks"]["redis"] == "ok"
    assert set(ready["checks"]["providers"]) == {"here_routing", "here_matrix", "osrm"}
    metrics = await app_client.get("/metrics")
    assert "http_requests_total" in metrics.text


async def test_auth_and_roles(app_client, settings, ops_headers, admin_headers) -> None:  # type: ignore[no-untyped-def]
    unauth = await app_client.get("/api/v1/trips", params={"date": "2026-10-06"})
    assert unauth.status_code == 401
    assert unauth.headers["content-type"].startswith("application/problem+json")
    assert unauth.json()["code"] == "missing_token"
    bad = await app_client.post(
        "/api/v1/auth/login", json={"email": settings.admin_email, "password": "nope"}
    )
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_credentials"
    me = await app_client.get("/api/v1/auth/me", headers=ops_headers)
    assert me.json() == {"email": "ops@blurabbit.test", "role": "ops"}
    forbidden = await app_client.post(
        "/api/v1/admin/synthetic/regenerate",
        json={"seed": 1, "days_past": 0, "days_future": 0},
        headers=ops_headers,
    )
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "admin_required"


async def test_login_rate_limit(app_client, settings) -> None:  # type: ignore[no-untyped-def]
    codes = []
    for _ in range(7):
        r = await app_client.post(
            "/api/v1/auth/login", json={"email": settings.admin_email, "password": "wrong"}
        )
        codes.append(r.status_code)
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


async def test_trips_listing_after_seed(app_client, admin_headers, clean, settings) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=2, days_future=2)
    tomorrow = today + timedelta(days=1)
    r = await app_client.get(
        "/api/v1/trips", params={"date": tomorrow.isoformat(), "limit": 50}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] > 80 and len(body["items"]) == 50 and body["next_cursor"]
    r2 = await app_client.get(
        "/api/v1/trips",
        params={"date": tomorrow.isoformat(), "limit": 50, "cursor": body["next_cursor"]},
        headers=admin_headers,
    )
    assert r2.json()["items"][0]["trip_id"] != body["items"][0]["trip_id"]
    trip_id = body["items"][0]["trip_id"]
    detail = await app_client.get(f"/api/v1/trips/{trip_id}", headers=admin_headers)
    assert detail.status_code == 200 and detail.json()["trip"]["trip_id"] == trip_id
    past = await app_client.get(
        "/api/v1/trips",
        params={"date": (today - timedelta(days=1)).isoformat(), "status": "completed"},
        headers=admin_headers,
    )
    assert past.json()["total"] > 50
    assert all(t["actual_drop_at"] for t in past.json()["items"])
    drivers = await app_client.get(
        "/api/v1/drivers", params={"date": tomorrow.isoformat()}, headers=admin_headers
    )
    assert len(drivers.json()) == 30
    vehicles = await app_client.get("/api/v1/vehicles", headers=admin_headers)
    assert len(vehicles.json()) == 30
    missing = await app_client.get(
        "/api/v1/trips/00000000-0000-0000-0000-000000000000", headers=admin_headers
    )
    assert missing.status_code == 404 and missing.json()["code"] == "trip_not_found"


async def test_snapshot_dedupe(clean, settings) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    first = await generate_dataset(clean.factory, settings, seed=7, today=today, days_past=0, days_future=0)
    second = await generate_dataset(clean.factory, settings, seed=7, today=today, days_past=0, days_future=0)
    assert first.snapshots_created == 1 and second.snapshots_created == 0


async def test_soc_checkin_and_validation(app_client, admin_headers, clean, settings) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=0, days_future=0)
    vehicles = (await app_client.get("/api/v1/vehicles", headers=admin_headers)).json()
    vid = vehicles[0]["vehicle_id"]
    ok = await app_client.post(
        f"/api/v1/vehicles/{vid}/soc-checkin",
        json={"soc_pct": 62.5, "odometer_km": 12000},
        headers=admin_headers,
    )
    assert ok.status_code == 201, ok.text
    again = (await app_client.get("/api/v1/vehicles", headers=admin_headers)).json()
    assert next(v for v in again if v["vehicle_id"] == vid)["soc_pct"] == 62.5
    bad = await app_client.post(
        f"/api/v1/vehicles/{vid}/soc-checkin", json={"soc_pct": 140}, headers=admin_headers
    )
    assert bad.status_code == 422 and bad.json()["code"] == "validation_error"


async def test_regenerate_endpoint(app_client, admin_headers) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    r = await app_client.post(
        "/api/v1/admin/synthetic/regenerate",
        json={"seed": 3, "days_past": 1, "days_future": 1},
        headers=admin_headers,
    )
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    for _ in range(100):
        run = (await app_client.get(f"/api/v1/jobs/runs/{run_id}", headers=admin_headers)).json()
        if run["status"] not in ("queued", "running"):
            break
        await asyncio.sleep(0.1)
    assert run["status"] == "succeeded", run
    assert run["stats"]["days"] == 3
    snaps = (await app_client.get("/api/v1/snapshots", headers=admin_headers)).json()
    assert len(snaps) == 3
