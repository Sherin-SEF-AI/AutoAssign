from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.adapters.synthetic.writer import generate_dataset
from app.db.models import Override, PlanAssignment
from app.db.session import session_scope
from app.eta.factory import build_estimator
from app.plan.repair import RepairRequest, repair
from app.plan.versions import get_plan

pytestmark = pytest.mark.integration


async def _setup(app_client, admin_headers, clean, settings) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    today = await clean.today()
    await generate_dataset(clean.factory, settings, seed=42, today=today, days_past=0, days_future=1)
    r = await app_client.put(
        "/api/v1/settings",
        json={"solver_solution_limit": 30, "incremental_solution_limit": 20},
        headers=admin_headers,
    )
    assert r.status_code == 200
    tomorrow = (today + timedelta(days=1)).isoformat()
    solved = await app_client.post(
        "/api/v1/plans/solve", json={"service_date": tomorrow}, headers=admin_headers
    )
    assert solved.status_code == 201, solved.text
    plan = solved.json()
    assert plan["version"] == 1 and plan["status"] == "draft"
    assert (
        plan["solver_params"]["threads"] == 1 and plan["solver_params"]["snapshot_id"] == plan["snapshot_id"]
    )
    detail = (await app_client.get(f"/api/v1/plans/{plan['plan_id']}", headers=admin_headers)).json()
    return tomorrow, detail


async def test_plan_lifecycle(app_client, admin_headers, ops_headers, clean, settings) -> None:  # type: ignore[no-untyped-def]
    day, detail = await _setup(app_client, admin_headers, clean, settings)
    plan_id = detail["plan"]["plan_id"]
    k = detail["plan"]["kpis"]
    assert k["assigned"] + k["unassigned"] == k["trips"] and k["assigned"] > 20
    assert all(u["unassigned_reason"] for u in detail["unassigned"])
    lanes = [lane for lane in detail["drivers"] if lane["available"]]
    busy = next(lane for lane in lanes if len(lane["assignments"]) >= 2)
    trip = busy["assignments"][0]
    # An infeasible move: put the trip on a driver whose shift has not started.
    target = next(
        lane
        for lane in lanes
        if lane["driver_id"] != busy["driver_id"] and lane["shift_start_at"] > trip["planned_pickup_at"]
    )
    rejected = await app_client.post(
        f"/api/v1/plans/{plan_id}/moves",
        json={"trip_id": trip["trip_id"], "to_driver_id": target["driver_id"]},
        headers=ops_headers,
    )
    body = rejected.json()
    assert rejected.status_code == 200 and body["accepted"] is False and body["plan"] is None
    assert any(
        e["code"] in ("shift", "time_window", "missing_leg", "skill", "vehicle_class", "account_approval")
        for e in body["errors"]
    )
    no_reason = await app_client.post(
        f"/api/v1/plans/{plan_id}/moves",
        json={"trip_id": trip["trip_id"], "to_driver_id": target["driver_id"], "force": True},
        headers=ops_headers,
    )
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "reason_required"
    forced = (
        await app_client.post(
            f"/api/v1/plans/{plan_id}/moves",
            json={
                "trip_id": trip["trip_id"],
                "to_driver_id": target["driver_id"],
                "force": True,
                "reason": "VIP asked for this driver",
            },
            headers=ops_headers,
        )
    ).json()
    assert forced["accepted"] and forced["forced"] and forced["plan"]["version"] == 2
    v2 = forced["plan"]["plan_id"]
    assert forced["plan"]["parent_plan_id"] == plan_id and forced["plan"]["trigger"] == "override"
    async with clean.factory() as s:
        ovs = (await s.execute(select(Override).order_by(Override.created_at))).scalars().all()
    assert [(o.accepted, o.forced) for o in ovs] == [(False, False), (True, True)]
    # Untouched drivers are copied verbatim.
    d1 = (await app_client.get(f"/api/v1/plans/{plan_id}", headers=ops_headers)).json()
    d2 = (await app_client.get(f"/api/v1/plans/{v2}", headers=ops_headers)).json()
    other = {lane["driver_id"]: lane for lane in d1["drivers"]}
    for lane in d2["drivers"]:
        if lane["driver_id"] in (busy["driver_id"], target["driver_id"]):
            continue
        assert lane["assignments"] == other[lane["driver_id"]]["assignments"]
    diff = (
        await app_client.get(f"/api/v1/plans/{v2}/diff", params={"against": plan_id}, headers=ops_headers)
    ).json()
    assert diff["changed_trips"] >= 1
    moved = next(t for t in diff["trips"] if t["trip_id"] == trip["trip_id"])
    assert moved["driver_before"] == busy["driver_id"] and moved["driver_after"] == target["driver_id"]
    per = {d["driver_id"]: d for d in diff["drivers"]}
    assert per[target["driver_id"]]["gained"] == 1 and per[busy["driver_id"]]["lost"] == 1
    # Lock another assignment, creating version 3.
    keep = next(
        lane
        for lane in d2["drivers"]
        if lane["assignments"] and lane["driver_id"] not in (busy["driver_id"], target["driver_id"])
    )
    locked_trip = keep["assignments"][0]["trip_id"]
    v3 = (
        await app_client.post(f"/api/v1/plans/{v2}/assignments/{locked_trip}/lock", headers=ops_headers)
    ).json()
    assert v3["version"] == 3 and v3["trigger"] == "lock"
    again = await app_client.post(f"/api/v1/plans/{v2}/assignments/{locked_trip}/lock", headers=ops_headers)
    assert again.status_code == 409 or again.json()["version"] == 4
    plans = (await app_client.get("/api/v1/plans", params={"date": day}, headers=ops_headers)).json()
    assert plans[0]["version"] >= 3
    latest = plans[0]["plan_id"]
    # Publishing needs every unassigned trip acknowledged.
    blocked = await app_client.post(f"/api/v1/plans/{latest}/publish", headers=ops_headers)
    if blocked.status_code == 422:
        assert blocked.json()["code"] == "unassigned_not_acknowledged"
        ack = await app_client.post(
            f"/api/v1/plans/{latest}/unassigned/acknowledge", json={"note": "reviewed"}, headers=ops_headers
        )
        assert ack.status_code == 200 and ack.json()["acknowledged"] >= 1
    pub = await app_client.post(f"/api/v1/plans/{latest}/publish", headers=ops_headers)
    assert pub.status_code == 200 and pub.json()["status"] == "published"
    twice = await app_client.post(f"/api/v1/plans/{latest}/publish", headers=ops_headers)
    assert twice.status_code == 409
    # The next re-solve respects the lock and leaves untouched drivers identical.
    async with session_scope(clean.factory) as s:
        plan = await get_plan(s, __import__("uuid").UUID(latest))
        st = await clean.settings()
        est = await build_estimator(st, clean.factory, clean.redis, clean.http)
        free_driver = __import__("uuid").UUID(keep["driver_id"])
        out = await repair(
            s,
            st,
            est,
            plan,
            RepairRequest(
                trigger="manual", reason="test", actor="test", free_drivers={free_driver}, include_float=False
            ),
        )
    assert out.outcome == "repaired" and out.plan is not None
    async with clean.factory() as s:
        before = (
            (await s.execute(select(PlanAssignment).where(PlanAssignment.plan_id == plan.plan_id)))
            .scalars()
            .all()
        )
        after = (
            (await s.execute(select(PlanAssignment).where(PlanAssignment.plan_id == out.plan.plan_id)))
            .scalars()
            .all()
        )
    a_by = {a.trip_id: a for a in after}
    assert a_by[__import__("uuid").UUID(locked_trip)].driver_id == free_driver
    assert a_by[__import__("uuid").UUID(locked_trip)].locked
    fields = (
        "driver_id",
        "seq",
        "planned_depart_at",
        "planned_pickup_at",
        "deadhead_s",
        "buffer_s",
        "energy_wh",
    )
    for b in before:
        if b.driver_id is not None and b.driver_id != free_driver:
            a = a_by[b.trip_id]
            assert tuple(getattr(a, f) for f in fields) == tuple(getattr(b, f) for f in fields)
    assert out.plan.status == "published"
    repairs = (await app_client.get("/api/v1/repairs", params={"date": day}, headers=ops_headers)).json()
    assert repairs[0]["trigger"] == "manual" and repairs[0]["outcome"] == "repaired"
