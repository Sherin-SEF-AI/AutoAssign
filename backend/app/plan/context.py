"""Rebuilds the planning view of an existing plan version.

Trip legs and the legs between consecutive stops come from the stored assignment rows, so an
unchanged driver evaluates exactly as it was planned. Anything new is estimated on demand.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import TripRecord
from app.config import Settings
from app.db.models import Plan, PlanAssignment, PlanRoute
from app.db.queries import plan_assignments, plan_routes
from app.eta.estimator import LegEstimator
from app.graph.build import Quantile, plannable_trips, trip_nodes, vehicle_contexts
from app.graph.model import Edge, Graph, HubLeg, TripNode, VehicleCtx
from app.plan.inputs import PlanningInputs, load_inputs
from app.plan.legs import LegBook


@dataclass(slots=True)
class PlanContext:
    plan: Plan
    settings: Settings
    inputs: PlanningInputs
    graph: Graph
    book: LegBook
    assignments: list[PlanAssignment]
    route_rows: dict[uuid.UUID, PlanRoute]
    routes: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)  # driver -> ordered trips

    @property
    def node_index(self) -> dict[uuid.UUID, int]:
        return self.graph.node_index()

    @property
    def vehicle_index(self) -> dict[uuid.UUID, int]:
        return self.graph.vehicle_index()

    def assignment(self, trip_id: uuid.UUID) -> PlanAssignment | None:
        return next((a for a in self.assignments if a.trip_id == trip_id), None)

    def seq_of(self, driver_id: uuid.UUID) -> list[int]:
        idx = self.node_index
        return [idx[t] for t in self.routes.get(driver_id, []) if t in idx]


def stored_overrides(
    assignments: Sequence[PlanAssignment], routes: dict[uuid.UUID, PlanRoute]
) -> tuple[
    dict[tuple[uuid.UUID, uuid.UUID], Edge],
    dict[tuple[uuid.UUID, uuid.UUID], HubLeg],
    dict[tuple[uuid.UUID, uuid.UUID], HubLeg],
]:
    """Legs as stored on each driver's route: edges between consecutive trips, start and end legs."""
    by_driver: dict[uuid.UUID, list[PlanAssignment]] = {}
    for a in assignments:
        if a.driver_id is not None and a.seq is not None:
            by_driver.setdefault(a.driver_id, []).append(a)
    edges: dict[tuple[uuid.UUID, uuid.UUID], Edge] = {}
    starts: dict[tuple[uuid.UUID, uuid.UUID], HubLeg] = {}
    ends: dict[tuple[uuid.UUID, uuid.UUID], HubLeg] = {}
    for driver_id, rows in by_driver.items():
        rows.sort(key=lambda r: r.seq or 0)
        first = rows[0]
        dh = first.deadhead_s or 0
        starts[(driver_id, first.trip_id)] = HubLeg(
            dh, dh, dh, first.deadhead_m or 0, first.deadhead_source or "stored", first.buffer_s or 0
        )
        for prev, cur in pairwise(rows):
            d = cur.deadhead_s or 0
            edges[(prev.trip_id, cur.trip_id)] = Edge(
                d, d, d, cur.deadhead_m or 0, cur.deadhead_source or "stored", cur.buffer_s or 0
            )
        route = routes.get(driver_id)
        if route is not None:
            ends[(rows[-1].trip_id, driver_id)] = HubLeg(
                route.end_leg_s, route.end_leg_s, route.end_leg_s, route.end_leg_m, "stored", 0
            )
    return edges, starts, ends


def apply_stored_trip_legs(nodes: list[TripNode], assignments: Sequence[PlanAssignment]) -> list[TripNode]:
    stored = {a.trip_id: a for a in assignments if a.trip_s is not None}
    out = []
    for n in nodes:
        a = stored.get(n.trip_id)
        if a is None or a.trip_s is None:
            out.append(n)
            continue
        out.append(
            replace(
                n,
                service_s=a.trip_s,
                trip_p80_s=a.trip_s if n.trip_type != "package" else n.trip_p80_s,
                trip_p50_s=a.trip_p50_s or n.trip_p50_s,
                trip_p90_s=max(n.trip_p90_s, a.trip_s),
                trip_m=a.trip_m if a.trip_m is not None else n.trip_m,
                trip_source=a.trip_source or n.trip_source,
            )
        )
    return out


async def load_plan_context(
    session: AsyncSession,
    plan: Plan,
    settings: Settings,
    estimator: LegEstimator,
    *,
    inputs: PlanningInputs | None = None,
    trips: list[TripRecord] | None = None,
    vehicles: list[VehicleCtx] | None = None,
) -> PlanContext:
    inputs = inputs or await load_inputs(session, plan.service_date, snapshot_id=plan.snapshot_id)
    assignments = list(await plan_assignments(session, plan.plan_id))
    route_rows = {r.driver_id: r for r in await plan_routes(session, plan.plan_id)}
    quantile: Quantile = "p90" if plan.solver_params.get("quantile") == "p90" else "p80"
    planned = plannable_trips(trips if trips is not None else inputs.trips)
    # Trips that are on the plan stay in the model even when their status moved on (started, completed).
    on_plan = {a.trip_id for a in assignments}
    extra = [
        t for t in (trips if trips is not None else inputs.trips) if t.trip_id in on_plan and t not in planned
    ]
    planned = sorted([*planned, *extra], key=lambda t: str(t.trip_id))
    nodes = apply_stored_trip_legs(
        await trip_nodes(plan.service_date, planned, settings, estimator, quantile), assignments
    )
    if vehicles is None:
        soc = {r.vehicle_id: r.soc_fraction * 100 for r in route_rows.values()}
        vehicles = vehicle_contexts(plan.service_date, inputs.drivers, inputs.vehicles, settings, soc)
    graph = Graph(
        service_date=plan.service_date,
        nodes=nodes,
        vehicles=vehicles,
        buffer_policy=str(plan.solver_params.get("buffer_policy", "fixed")),
    )
    edges, starts, ends = stored_overrides(assignments, route_rows)
    idx = graph.node_index()
    vidx = graph.vehicle_index()
    for (a, b), e in edges.items():
        if a in idx and b in idx:
            graph.edges[(idx[a], idx[b])] = e
    for (d, t), leg in starts.items():
        if d in vidx and t in idx:
            graph.start_legs[(vidx[d], idx[t])] = leg
    for (t, d), leg in ends.items():
        if d in vidx and t in idx:
            graph.end_legs[(idx[t], vidx[d])] = leg
    routes: dict[uuid.UUID, list[uuid.UUID]] = {}
    for row in sorted(
        (r for r in assignments if r.driver_id is not None), key=lambda r: (str(r.driver_id), r.seq or 0)
    ):
        assert row.driver_id is not None
        routes.setdefault(row.driver_id, []).append(row.trip_id)
    book = LegBook(graph, settings, estimator, plan.service_date)
    return PlanContext(plan, settings, inputs, graph, book, assignments, route_rows, routes)
