"""OR-Tools routing model over a compatibility graph.

Vehicles are driver and vehicle pairs; each has its own start and end node. Trip nodes are
ordered by trip_id and vehicles by driver_id so identical inputs give identical models.
Dimensions: time (seconds from 00:00 IST), energy (Wh), count (trips, soft upper bound).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.config import Settings
from app.core.logging import get_logger
from app.graph.model import Graph
from app.solver.evaluate import leg_energy_wh

log = get_logger("solver")

HORIZON_S = 30 * 3600
PIN_PENALTY_FACTOR = 10


class SolveError(Exception):
    pass


@dataclass(slots=True)
class SolveOptions:
    time_limit_s: int
    solution_limit: int
    mode: str = "full"  # full or incremental
    initial_routes: Mapping[int, Sequence[int]] | None = None
    locked_vehicles: frozenset[int] = frozenset()
    pinned: Mapping[int, int] = field(default_factory=dict)  # node -> vehicle (ops locks)
    first_solution: str | None = None

    def params(self, settings: Settings) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "first_solution": "PATH_CHEAPEST_ARC",
            "metaheuristic": "GUIDED_LOCAL_SEARCH",
            "time_limit_s": self.time_limit_s,
            "solution_limit": self.solution_limit,
            "threads": 1,
            "drop_penalty_s": settings.drop_penalty_s,
            "deadhead_m_weight": settings.deadhead_m_weight,
            "max_trips_per_driver": settings.max_trips_per_driver,
            "fairness_cost_s": settings.fairness_cost_s,
            "early_arrival_s": settings.early_arrival_s,
            "max_gap_s": settings.max_gap_s,
            "p_aux_w": settings.p_aux_w,
            "locked_vehicles": len(self.locked_vehicles),
            "pinned_trips": len(self.pinned),
        }


@dataclass(slots=True)
class SolveResult:
    routes: dict[int, list[int]]
    dropped: list[int]
    energy_cumul: dict[int, int]
    objective: int
    status: str
    solve_s: float
    solutions: int
    excluded_nodes: list[int] = field(default_factory=list)
    stop_reason: str = "solution_limit"


def _allow_vehicles(routing: pywrapcp.RoutingModel, allowed: list[int], idx: int) -> None:
    """SetAllowedVehiclesForIndex, or the equivalent vehicle variable domain when this OR-Tools
    build cannot convert a Python list to absl::Span (seen in 9.15 wheels)."""
    try:
        routing.SetAllowedVehiclesForIndex(allowed, idx)
    except TypeError:
        routing.VehicleVar(idx).SetValues([-1, *allowed])


def _cost(seconds: int, metres: int, weight: float) -> int:
    return round(seconds + weight * metres)


def solve(graph: Graph, settings: Settings, opts: SolveOptions) -> SolveResult:
    started = time.perf_counter()
    nodes, vehicles = graph.nodes, graph.vehicles
    n, nv = len(nodes), len(vehicles)
    # Nodes with no eligible vehicle cannot be modelled usefully; they are dropped up front.
    excluded = [i for i, nd in enumerate(nodes) if not any(v.eligible(nd) for v in vehicles)]
    if nv == 0:
        return SolveResult({}, list(range(n)), {}, 0, "no_vehicles", 0.0, 0, excluded)
    starts = [n + 2 * v for v in range(nv)]
    ends = [n + 2 * v + 1 for v in range(nv)]
    size = n + 2 * nv
    manager = pywrapcp.RoutingIndexManager(size, nv, starts, ends)
    routing = pywrapcp.RoutingModel(manager)
    big = HORIZON_S + 1
    w = settings.deadhead_m_weight

    time_cbs: list[int] = []
    energy_cbs: list[int] = []
    for v_idx, v in enumerate(vehicles):
        t = [[0] * size for _ in range(size)]
        e = [[0] * size for _ in range(size)]
        c = [[0] * size for _ in range(size)]
        s_node, e_node = starts[v_idx], ends[v_idx]
        for i in range(n):
            row_t, row_c = t[i], c[i]
            for j in range(n):
                row_t[j] = big
                row_c[j] = big
            for e_idx in range(nv):
                row_t[ends[e_idx]] = big
                row_c[ends[e_idx]] = big
            t[s_node][i] = big
            c[s_node][i] = big
        for (i, j), edge in graph.edges.items():
            t[i][j] = edge.transit_s(nodes[i])
            e[i][j] = leg_energy_wh(
                settings, v, edge.deadhead_m + nodes[j].trip_m, edge.deadhead_p80_s + nodes[j].service_s
            )
            c[i][j] = _cost(edge.deadhead_p80_s, edge.deadhead_m, w)
        for (sv, j), leg in graph.start_legs.items():
            if sv != v_idx:
                continue
            t[s_node][j] = leg.p80_s + leg.buffer_s
            e[s_node][j] = leg_energy_wh(settings, v, leg.m + nodes[j].trip_m, leg.p80_s + nodes[j].service_s)
            c[s_node][j] = _cost(leg.p80_s, leg.m, w)
        for (i, ev), leg in graph.end_legs.items():
            if ev != v_idx:
                continue
            t[i][e_node] = nodes[i].service_s + leg.p80_s
            e[i][e_node] = leg_energy_wh(settings, v, leg.m, leg.p80_s)
            c[i][e_node] = _cost(leg.p80_s, leg.m, w)
        t[s_node][e_node] = 0
        c[s_node][e_node] = 0
        time_cbs.append(routing.RegisterTransitMatrix(t))
        energy_cbs.append(routing.RegisterTransitMatrix(e))
        routing.SetArcCostEvaluatorOfVehicle(routing.RegisterTransitMatrix(c), v_idx)

    routing.AddDimensionWithVehicleTransits(time_cbs, settings.max_gap_s, HORIZON_S, False, "time")
    time_dim = routing.GetDimensionOrDie("time")
    caps = [max(0, v.energy_cap_wh - v.energy_used_wh) for v in vehicles]
    routing.AddDimensionWithVehicleTransitAndCapacity(energy_cbs, 0, caps, True, "energy")
    energy_dim = routing.GetDimensionOrDie("energy")
    count_cb = routing.RegisterUnaryTransitCallback(lambda idx: 1 if manager.IndexToNode(idx) < n else 0)
    routing.AddDimensionWithVehicleCapacity(count_cb, 0, [n] * nv, True, "count")
    count_dim = routing.GetDimensionOrDie("count")

    excluded_set = set(excluded)
    for v_idx, v in enumerate(vehicles):
        start_idx, end_idx = routing.Start(v_idx), routing.End(v_idx)
        time_dim.CumulVar(start_idx).SetRange(v.origin_s, v.shift_end_s)
        time_dim.CumulVar(end_idx).SetRange(v.origin_s, v.shift_end_s)
        time_dim.SetSpanUpperBoundForVehicle(int(v.max_hours * 3600), v_idx)
        routing.AddVariableMaximizedByFinalizer(time_dim.CumulVar(start_idx))
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(end_idx))
        count_dim.SetCumulVarSoftUpperBound(end_idx, settings.max_trips_per_driver, settings.fairness_cost_s)
        for j in range(n):
            if (v_idx, j) not in graph.start_legs:
                routing.NextVar(start_idx).RemoveValue(manager.NodeToIndex(j))
    for i, node in enumerate(nodes):
        idx = manager.NodeToIndex(i)
        if i in excluded_set:
            routing.AddDisjunction([idx], settings.drop_penalty_s)
            routing.VehicleVar(idx).SetValues([-1])
            continue
        time_dim.CumulVar(idx).SetRange(node.pickup_s, node.pickup_s)
        allowed = [v_idx for v_idx, v in enumerate(vehicles) if v.eligible(node)]
        pinned = opts.pinned.get(i)
        if pinned is not None:
            routing.AddDisjunction([idx], settings.drop_penalty_s * PIN_PENALTY_FACTOR)
            routing.VehicleVar(idx).SetValues([-1, pinned])
        else:
            routing.AddDisjunction([idx], settings.drop_penalty_s)
            _allow_vehicles(routing, allowed, idx)
        nxt = routing.NextVar(idx)
        for j in range(n):
            if j != i and (i, j) not in graph.edges:
                nxt.RemoveValue(manager.NodeToIndex(j))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = getattr(
        routing_enums_pb2.FirstSolutionStrategy, opts.first_solution or "PATH_CHEAPEST_ARC"
    )
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = opts.time_limit_s
    params.solution_limit = opts.solution_limit
    params.log_search = False
    routing.CloseModelWithParameters(params)

    initial = None
    if opts.initial_routes is not None:
        routes = [
            [manager.NodeToIndex(j) for j in opts.initial_routes.get(v_idx, []) if j not in excluded_set]
            for v_idx in range(nv)
        ]
        locks = [routes[v_idx] if v_idx in opts.locked_vehicles else [] for v_idx in range(nv)]
        if opts.locked_vehicles:
            if not routing.ApplyLocksToAllVehicles(locks, False):
                raise SolveError("locked routes are inconsistent with the model")
            for v_idx in opts.locked_vehicles:
                last = routes[v_idx][-1] if routes[v_idx] else routing.Start(v_idx)
                routing.NextVar(last).SetValue(routing.End(v_idx))
        initial = routing.ReadAssignmentFromRoutes(routes, True)
        if initial is None:
            initial = routing.ReadAssignmentFromRoutes(locks, True)
        if initial is None:
            raise SolveError("the current plan cannot seed the model (locked routes infeasible)")

    if initial is not None:
        solution = routing.SolveFromAssignmentWithParameters(initial, params)
    else:
        solution = routing.SolveWithParameters(params)
    elapsed = time.perf_counter() - started
    status_name = {
        0: "not_solved",
        1: "success",
        2: "fail",
        3: "fail_timeout",
        4: "invalid",
        5: "infeasible",
        7: "optimal",
    }.get(routing.status(), str(routing.status()))
    if solution is None:
        raise SolveError(f"no solution found ({status_name})")
    out_routes: dict[int, list[int]] = {}
    energy: dict[int, int] = {}
    visited: set[int] = set()
    for v_idx in range(nv):
        idx = routing.Start(v_idx)
        seq: list[int] = []
        while not routing.IsEnd(idx):
            idx = solution.Value(routing.NextVar(idx))
            node = manager.IndexToNode(idx)
            if node < n:
                seq.append(node)
                visited.add(node)
        out_routes[v_idx] = seq
        energy[v_idx] = solution.Value(energy_dim.CumulVar(routing.End(v_idx)))
    dropped = [i for i in range(n) if i not in visited]
    solutions = int(routing.solver().Solutions())
    return SolveResult(
        routes=out_routes,
        dropped=dropped,
        energy_cumul=energy,
        objective=int(solution.ObjectiveValue()),
        status=status_name,
        solve_s=round(elapsed, 3),
        solutions=solutions,
        excluded_nodes=excluded,
        stop_reason="time_limit" if elapsed >= opts.time_limit_s - 0.25 else "solution_limit",
    )


def trip_ids(graph: Graph, idxs: Sequence[int]) -> list[uuid.UUID]:
    return [graph.nodes[i].trip_id for i in idxs]
