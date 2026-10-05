from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.synthetic.writer import generate_dataset
from app.jobs.late_booking import QUEUE

pytestmark = pytest.mark.integration
SVC = {"X-API-Key": "svc-test-key"}


async def test_trip_events_and_pings(app_client, admin_headers, clean, settings) -> None:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=0, days_future=1)
    day = (today + timedelta(days=1)).isoformat()
    trips = (
        await app_client.get("/api/v1/trips", params={"date": day, "status": "booked"}, headers=admin_headers)
    ).json()
    trip = trips["items"][0]
    denied = await app_client.post("/api/v1/events/trip", json={}, headers={"X-API-Key": "nope"})
    assert denied.status_code == 401 and denied.json()["code"] == "invalid_api_key"
    jwt_only = await app_client.post("/api/v1/pings", json={"pings": []}, headers=admin_headers)
    assert jwt_only.status_code == 401
    event = {
        "event_id": "e-1",
        "event_type": "trip.cancelled",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trip_id": trip["trip_id"],
        "service_date": day,
    }
    r = await app_client.post("/api/v1/events/trip", json=event, headers=SVC)
    assert r.status_code == 202 and r.json()["replan_queued"] and not r.json()["duplicate"]
    dup = await app_client.post("/api/v1/events/trip", json=event, headers=SVC)
    assert dup.json()["duplicate"] and not dup.json()["replan_queued"]
    assert await clean.redis.llen(QUEUE) == 1
    after = (await app_client.get(f"/api/v1/trips/{trip['trip_id']}", headers=admin_headers)).json()
    assert after["trip"]["status"] == "cancelled"
    status = {
        "event_id": "e-2",
        "event_type": "trip.status",
        "occurred_at": datetime.now(UTC).isoformat(),
        "trip_id": trips["items"][1]["trip_id"],
        "service_date": day,
        "status": "en_route",
    }
    assert (await app_client.post("/api/v1/events/trip", json=status, headers=SVC)).status_code == 202
    listed = (
        await app_client.get(
            "/api/v1/trips", params={"date": day, "status": "en_route"}, headers=admin_headers
        )
    ).json()
    assert listed["total"] == 1
    bad = await app_client.post(
        "/api/v1/events/trip", json={**status, "event_id": "e-3", "status": None}, headers=SVC
    )
    assert bad.status_code == 422
    driver = str(uuid.uuid4())
    pings = [
        {
            "driver_id": driver,
            "ts": (datetime.now(UTC) - timedelta(seconds=30 * i)).isoformat(),
            "lat": 12.97,
            "lng": 77.6,
            "speed_mps": 5.0,
            "accuracy_m": 8.0,
        }
        for i in range(5)
    ]
    p = await app_client.post("/api/v1/pings", json={"pings": pings}, headers=SVC)
    assert p.status_code == 202 and p.json() == {"received": 5, "stored": 5}
    again = await app_client.post("/api/v1/pings", json={"pings": pings}, headers=SVC)
    assert again.json()["stored"] == 0
    big = await app_client.post(
        "/api/v1/pings",
        content=b"x" * (settings.pings_max_body_bytes + 1),
        headers={
            **SVC,
            "Content-Type": "application/json",
            "Content-Length": str(settings.pings_max_body_bytes + 1),
        },
    )
    assert big.status_code == 413 and big.json()["code"] == "payload_too_large"


async def test_sim_controls(app_client, admin_headers, ops_headers) -> None:  # type: ignore[no-untyped-def]
    st = (await app_client.get("/api/v1/admin/sim/status", headers=ops_headers)).json()
    assert st["exists"] is False
    assert (await app_client.post("/api/v1/admin/sim/start", headers=ops_headers)).status_code == 403
    started = (
        await app_client.post("/api/v1/admin/sim/start", json={"speed": 120}, headers=admin_headers)
    ).json()
    assert started["running"] and started["speed"] == 120 and all(started["disturbances"].values())
    paused = (await app_client.post("/api/v1/admin/sim/pause", headers=admin_headers)).json()
    assert paused["running"] is False
    d = (
        await app_client.post(
            "/api/v1/admin/sim/disturbances", json={"slow_legs": False}, headers=admin_headers
        )
    ).json()
    assert d["disturbances"]["slow_legs"] is False
    reset = (await app_client.post("/api/v1/admin/sim/reset", headers=admin_headers)).json()
    assert reset["exists"] is False
    live = (await app_client.get("/api/v1/live", params={"date": "2026-10-06"}, headers=ops_headers)).json()
    assert live["drivers"] == []
