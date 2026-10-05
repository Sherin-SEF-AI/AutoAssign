"""Solver invariants on generated instances."""

from __future__ import annotations

import random
import uuid
from datetime import date

import pytest

from app.core.geo import Point
from app.eta.estimator import LegEstimator
from app.eta.factors import FactorTable, default_factors
from app.graph.build import build_graph
from app.graph.model import Graph, TripNode, VehicleCtx
from app.solver.extract import REASONS, extract
from app.solver.model import SolveOptions, solve

pytestmark = pytest.mark.integration
D = date(2026, 10, 6)
CENTER = Point(12.97, 77.62)


def _pt(rng: random.Random, km: float = 8.0) -> Point:
    return Point(CENTER.lat + rng.uniform(-km, km) / 111, CENTER.lng + rng.uniform(-km, km) / 108)


def instance(seed: int, n_trips: int = 40, n_veh: int = 8) -> tuple[list[TripNode], list[VehicleCtx]]:
    rng = random.Random(seed)
    nodes = []
    for _ in range(n_trips):
        svc = rng.randint(15, 70) * 60
        cls = "suv" if rng.random() < 0.2 else "any"
        ttype = "airport" if rng.random() < 0.15 else "city"
        acct = "A1" if rng.random() < 0.08 else None
        nodes.append(
            TripNode(
                trip_id=uuid.UUID(int=rng.getrandbits(128)),
                pickup=_pt(rng),
                drop=_pt(rng),
                pickup_s=rng.randint(6 * 60, 21 * 60) * 60,
                service_s=svc,
                trip_p50_s=svc - 300,
                trip_p80_s=svc,
                trip_p90_s=svc + 300,
                trip_m=rng.randint(3, 25) * 1000,
                trip_source="fallback",
                trip_type=ttype,
                vehicle_class=cls,
                account_id=acct,
            )
        )
    nodes.sort(key=lambda t: str(t.trip_id))
    vehicles = []
    for v in range(n_veh):
        start_h = (5, 9, 13)[v % 3]
        vehicles.append(
            VehicleCtx(
                driver_id=uuid.UUID(int=10_000 + v),
                vehicle_id=uuid.UUID(int=20_000 + v),
                driver_name=f"d{v}",
                model="windsor" if v % 2 else "tigor_ev",
                vehicle_class="suv" if v % 2 else "sedan",
                start=_pt(rng, 5),
                end=_pt(rng, 5),
                shift_start_s=start_h * 3600,
                shift_end_s=(start_h + 10) * 3600,
                max_hours=10,
                skills=("airport",) if v % 3 != 1 else (),
                approved_accounts=("A1",) if v == 0 else (),
                e_roll_wh_per_km=123 if v % 2 else 137,
                planning_cap_km=220 if v % 2 else 140,
                soc_fraction=0.6 if v == 3 else 1.0,
            )
        )
    return nodes, vehicles


async def graph_for(clean, seed: int, **kw) -> tuple[Graph, object]:  # type: ignore[no-untyped-def]
    s = clean.base_settings.model_copy(update={"here_enabled": False})
    est = LegEstimator(
        settings=s,
        factory=clean.factory,
        redis=clean.redis,
        factors=FactorTable(defaults=default_factors(s)),
        persist=False,
    )
    nodes, vehicles = instance(seed, **kw)
    return await build_graph(D, nodes, vehicles, s, est), s


@pytest.mark.parametrize("seed", [1, 2, 3])
async def test_invariants(clean, seed) -> None:  # type: ignore[no-untyped-def]
    g, s = await graph_for(clean, seed)
    r = solve(g, s, SolveOptions(time_limit_s=20, solution_limit=60))
    ex = extract(g, s, r)
    seen: set[int] = set()
    for v_idx, ev in ex.routes.items():
        veh = g.vehicles[v_idx]
        assert ev.feasible, ev.errors
        prev_end = None
        for stop in ev.stops:
            node = g.nodes[stop.node]
            assert stop.node not in seen
            seen.add(stop.node)
            assert veh.eligible(node), "allowed vehicle rules"
            assert stop.pickup_s == node.pickup_s
            # Earliest arrival (pickup minus slack) plus buffer must not pass the pickup time.
            assert stop.slack_s >= stop.buffer_s, "pickup inside its window with buffer"
            assert node.pickup_s - s.early_arrival_s <= stop.arrive_pickup_s <= node.pickup_s
            if prev_end is not None:
                assert prev_end + stop.deadhead_s + stop.buffer_s <= node.pickup_s, "no overlapping legs"
            prev_end = node.end_s
        if ev.stops:
            assert ev.start_s is not None and ev.end_s is not None
            assert veh.shift_start_s <= ev.start_s and ev.end_s <= veh.shift_end_s, "within shift"
            assert ev.energy_wh <= veh.energy_cap_wh, "within energy cap"
            assert ev.energy_wh == r.energy_cumul[v_idx]
    assert seen.isdisjoint(r.dropped) and len(seen) + len(r.dropped) == len(g.nodes)
    assert set(ex.unassigned) == set(r.dropped)
    assert all(reason in REASONS for reason in ex.unassigned.values())


async def test_deterministic(clean) -> None:  # type: ignore[no-untyped-def]
    g, s = await graph_for(clean, 7)
    opts = SolveOptions(time_limit_s=30, solution_limit=80)
    a, b = solve(g, s, opts), solve(g, s, opts)
    assert a.routes == b.routes and a.objective == b.objective and a.stop_reason == "solution_limit"


async def test_no_eligible_vehicle_is_dropped_with_reason(clean) -> None:  # type: ignore[no-untyped-def]
    g, s = await graph_for(clean, 4, n_trips=10, n_veh=2)
    bad = TripNode(
        trip_id=uuid.UUID(int=1),
        pickup=CENTER,
        drop=CENTER,
        pickup_s=10 * 3600,
        service_s=600,
        trip_p50_s=500,
        trip_p80_s=600,
        trip_p90_s=700,
        trip_m=1000,
        trip_source="fallback",
        trip_type="city",
        vehicle_class="suv",
        account_id="ZZ",
    )
    g.nodes.append(bad)
    r = solve(g, s, SolveOptions(time_limit_s=10, solution_limit=20))
    ex = extract(g, s, r)
    assert ex.unassigned[len(g.nodes) - 1] == "no_eligible_vehicle"


async def test_incremental_keeps_locked_vehicles_identical(clean) -> None:  # type: ignore[no-untyped-def]
    g, s = await graph_for(clean, 11)
    base = solve(g, s, SolveOptions(time_limit_s=20, solution_limit=60))
    used = [v for v, seq in base.routes.items() if seq]
    free = {used[0]}
    locked = frozenset(v for v in base.routes if v not in free)
    inc = solve(
        g,
        s,
        SolveOptions(
            time_limit_s=10,
            solution_limit=40,
            mode="incremental",
            initial_routes=base.routes,
            locked_vehicles=locked,
        ),
    )
    for v in locked:
        assert inc.routes[v] == base.routes[v]
    # The free vehicle may change, but only it, and it never loses feasibility.
    ex = extract(g, s, inc)
    assert all(ev.feasible for ev in ex.routes.values())


async def test_pinned_trip_stays_on_vehicle(clean) -> None:  # type: ignore[no-untyped-def]
    g, s = await graph_for(clean, 12)
    base = solve(g, s, SolveOptions(time_limit_s=20, solution_limit=60))
    v, seq = next((v, seq) for v, seq in base.routes.items() if seq)
    pinned = {seq[0]: v}
    inc = solve(
        g,
        s,
        SolveOptions(
            time_limit_s=10, solution_limit=40, mode="incremental", initial_routes=base.routes, pinned=pinned
        ),
    )
    assert seq[0] in inc.routes[v]


async def test_soc_reduces_capacity(clean) -> None:  # type: ignore[no-untyped-def]
    g, _ = await graph_for(clean, 1)
    assert g.vehicles[3].energy_cap_wh == int(220 * 123 * 0.6)
