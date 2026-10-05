from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.geo import Point
from app.eta.estimator import LegEstimator
from app.eta.factors import FactorTable, default_factors
from app.graph.build import buffer_s, build_graph
from app.graph.model import TripNode, VehicleCtx

pytestmark = pytest.mark.integration
D = date(2026, 10, 6)


def node(i: int, pickup_h: float, at: Point, to: Point, service_s: int = 1800, **kw) -> TripNode:  # type: ignore[no-untyped-def]
    return TripNode(
        trip_id=uuid.UUID(int=i),
        pickup=at,
        drop=to,
        pickup_s=int(pickup_h * 3600),
        service_s=service_s,
        trip_p50_s=service_s - 200,
        trip_p80_s=service_s,
        trip_p90_s=service_s + 200,
        trip_m=10000,
        trip_source="fallback",
        trip_type=kw.get("trip_type", "city"),
        vehicle_class=kw.get("cls", "any"),
        account_id=kw.get("account"),
    )


def vehicle(i: int, start_h: int, end_h: int, **kw) -> VehicleCtx:  # type: ignore[no-untyped-def]
    home = Point(12.93, 77.62)
    return VehicleCtx(
        driver_id=uuid.UUID(int=1000 + i),
        vehicle_id=uuid.UUID(int=2000 + i),
        driver_name=f"d{i}",
        model="tigor_ev",
        vehicle_class=kw.get("cls", "sedan"),
        start=home,
        end=home,
        shift_start_s=start_h * 3600,
        shift_end_s=end_h * 3600,
        max_hours=10,
        skills=tuple(kw.get("skills", ())),
        approved_accounts=tuple(kw.get("accounts", ())),
        e_roll_wh_per_km=137,
        planning_cap_km=140,
    )


def test_buffer_policies(settings) -> None:  # type: ignore[no-untyped-def]
    fixed = buffer_s(
        settings, "fixed", trip_p50=900, trip_p80=1000, trip_p90=1100, leg_p50=500, leg_p80=600, leg_p90=700
    )
    assert fixed == 900 + round(0.10 * 1600)
    q = buffer_s(
        settings,
        "quantile",
        trip_p50=900,
        trip_p80=1000,
        trip_p90=1100,
        leg_p50=500,
        leg_p80=600,
        leg_p90=700,
    )
    assert q == 300  # max(BUFFER_MIN_S, 100 + 100)
    big = buffer_s(
        settings, "quantile", trip_p50=0, trip_p80=1000, trip_p90=2000, leg_p50=0, leg_p80=0, leg_p90=0
    )
    assert big == 1000


def test_eligibility() -> None:
    v = vehicle(1, 5, 15)
    assert not v.eligible(node(1, 9, Point(0, 0), Point(0, 0), cls="suv"))
    assert not v.eligible(node(1, 9, Point(0, 0), Point(0, 0), trip_type="airport"))
    assert not v.eligible(node(1, 9, Point(0, 0), Point(0, 0), account="A1"))
    assert vehicle(2, 5, 15, cls="suv", skills=["airport"], accounts=["A1"]).eligible(
        node(1, 9, Point(0, 0), Point(0, 0), cls="suv", trip_type="airport", account="A1")
    )


async def test_candidate_edges_and_feasibility(clean) -> None:  # type: ignore[no-untyped-def]
    s = clean.base_settings.model_copy(update={"here_enabled": False})
    est = LegEstimator(
        settings=s, factory=clean.factory, redis=clean.redis, factors=FactorTable(defaults=default_factors(s))
    )
    a, b = Point(12.93, 77.62), Point(12.95, 77.64)
    far = Point(13.25, 77.62)  # about 35 km from b
    nodes = [
        node(1, 8.0, a, b),
        node(2, 10.0, b, a),  # reachable after 1
        node(3, 8.6, b, a),  # too close in time after 1 (overlap)
        node(4, 13.5, far, a),  # gap fine but deadhead > 25 km from 1's drop
        node(5, 13.0, a, b),  # gap > 4 h after 1 is false (4.5h) -> 13.0-8.5 = 4.5 h > MAX_GAP
    ]
    g = await build_graph(D, nodes, [vehicle(1, 5, 23)], s, est)
    assert (0, 1) in g.edges
    assert (0, 2) not in g.edges
    assert (0, 3) not in g.edges
    assert (0, 4) not in g.edges
    e = g.edges[(0, 1)]
    assert nodes[0].pickup_s + e.transit_s(nodes[0]) <= nodes[1].pickup_s
    assert g.stats["node_count"] == 5 and g.stats["buffer_policy"] == "fixed"
    assert (0, 0) in g.start_legs and (1, 0) in g.end_legs
