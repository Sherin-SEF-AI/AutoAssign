"""Compatibility graph: trip legs, candidate deadhead edges, hub legs, buffers.

A driver can take trip B after trip A only if
    pickup_A + service_A + deadhead_p80(drop_A, pickup_B) + buffer <= pickup_B.
Only candidate pairs (gap within MAX_GAP_S, straight line within MAX_DEADHEAD_KM) get deadhead
estimates, and only feasible ones become edges.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Literal

from app.adapters.base import DriverRecord, TripRecord, VehicleRecord
from app.config import Settings
from app.core.geo import Point, haversine_m
from app.core.speed import free_flow_kmh
from app.core.timeutil import day_start_utc, seconds_since_day_start
from app.eta.estimator import LegEstimator
from app.eta.types import LegEstimate, LegRequest
from app.graph.model import PLANNABLE_STATUSES, Edge, Graph, HubLeg, TripNode, VehicleCtx

Quantile = Literal["p80", "p90"]


def _q(est: LegEstimate, quantile: Quantile) -> int:
    return round(est.p90_s if quantile == "p90" else est.p80_s)


def buffer_s(
    settings: Settings,
    policy: str,
    *,
    trip_p50: int,
    trip_p80: int,
    trip_p90: int,
    leg_p50: int,
    leg_p80: int,
    leg_p90: int,
) -> int:
    """Fixed policy before calibration, quantile spread policy after."""
    if policy == "fixed":
        return round(settings.buffer_fixed_s + settings.buffer_pct * (trip_p80 + leg_p80))
    spread = (trip_p90 - trip_p80) + (leg_p90 - leg_p80)
    return max(settings.buffer_min_s, round(spread))


def plannable_trips(trips: Sequence[TripRecord]) -> list[TripRecord]:
    return sorted((t for t in trips if t.status in PLANNABLE_STATUSES), key=lambda t: str(t.trip_id))


def vehicle_contexts(
    service_date: date,
    drivers: Sequence[DriverRecord],
    vehicles: Sequence[VehicleRecord],
    settings: Settings,
    soc_pct: Mapping[uuid.UUID, float] | None = None,
) -> list[VehicleCtx]:
    """Driver and vehicle pairs on shift, excluding Uber shifts and vehicles in service."""
    by_id = {v.vehicle_id: v for v in vehicles}
    out: list[VehicleCtx] = []
    for d in sorted(drivers, key=lambda x: str(x.driver_id)):
        if not d.active or d.channel_today == "uber" or d.vehicle_id is None:
            continue
        if d.shift_start_at is None or d.shift_end_at is None:
            continue
        v = by_id.get(d.vehicle_id)
        if v is None or v.status != "active":
            continue
        soc = (soc_pct or {}).get(v.vehicle_id)
        out.append(
            VehicleCtx(
                driver_id=d.driver_id,
                vehicle_id=v.vehicle_id,
                driver_name=d.name,
                model=v.model,
                vehicle_class=v.vehicle_class,
                start=Point(d.start_lat, d.start_lng),
                end=Point(d.end_lat, d.end_lng),
                shift_start_s=seconds_since_day_start(d.shift_start_at, service_date),
                shift_end_s=seconds_since_day_start(d.shift_end_at, service_date),
                max_hours=d.max_hours,
                skills=tuple(sorted(d.skills)),
                approved_accounts=tuple(sorted(d.approved_accounts)),
                e_roll_wh_per_km=settings.e_roll_json[v.model],
                planning_cap_km=settings.range_caps_json[v.model],
                soc_fraction=min(1.0, max(0.0, soc / 100.0)) if soc is not None else 1.0,
            )
        )
    return out


async def trip_nodes(
    service_date: date,
    trips: Sequence[TripRecord],
    settings: Settings,
    estimator: LegEstimator,
    quantile: Quantile = "p80",
) -> list[TripNode]:
    day0 = day_start_utc(service_date)
    road = [t for t in trips if t.trip_type != "package"]
    ests = await estimator.estimate_many(
        [LegRequest(Point(t.pickup_lat, t.pickup_lng), Point(t.drop_lat, t.drop_lng), t.scheduled_pickup_at) for t in road],
        "trip",
    )
    by_trip = {t.trip_id: e for t, e in zip(road, ests, strict=True)}
    nodes: list[TripNode] = []
    for t in trips:
        pickup_s = int((t.scheduled_pickup_at - day0).total_seconds())
        common = {
            "trip_id": t.trip_id,
            "pickup": Point(t.pickup_lat, t.pickup_lng),
            "drop": Point(t.drop_lat, t.drop_lng),
            "pickup_s": pickup_s,
            "trip_type": t.trip_type,
            "vehicle_class": t.vehicle_class,
            "account_id": t.account_id,
            "vip": bool(t.tags.get("vip")),
        }
        if t.trip_type == "package":
            hours = t.package_hours or 4.0
            secs = round(hours * 3600)
            nodes.append(
                TripNode(
                    **common,  # type: ignore[arg-type]
                    service_s=secs,
                    trip_p50_s=secs,
                    trip_p80_s=secs,
                    trip_p90_s=secs,
                    trip_m=round(hours * settings.package_km_per_hour * 1000),
                    trip_source="package",
                )
            )
            continue
        e = by_trip[t.trip_id]
        p50, p80, p90, m = e.as_ints()
        nodes.append(
            TripNode(
                **common,  # type: ignore[arg-type]
                service_s=_q(e, quantile),
                trip_p50_s=p50,
                trip_p80_s=p80,
                trip_p90_s=p90,
                trip_m=m,
                trip_source=e.source,
            )
        )
    return nodes


async def build_graph(
    service_date: date,
    nodes: list[TripNode],
    vehicles: list[VehicleCtx],
    settings: Settings,
    estimator: LegEstimator,
    *,
    quantile: Quantile = "p80",
    edge_overrides: Mapping[tuple[uuid.UUID, uuid.UUID], Edge] | None = None,
) -> Graph:
    started = time.perf_counter()
    day0 = day_start_utc(service_date)
    policy = "quantile" if estimator.factors.calibrated else "fixed"
    graph = Graph(service_date=service_date, nodes=nodes, vehicles=vehicles, buffer_policy=policy)
    overrides = dict(edge_overrides or {})
    max_dh_m = settings.max_deadhead_km * 1000

    # Candidate deadhead edges.
    candidates: list[tuple[int, int]] = []
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if i == j:
                continue
            gap = b.pickup_s - a.end_s
            if gap < 0 or gap > settings.max_gap_s:
                continue
            if haversine_m(a.drop, b.pickup) > max_dh_m:
                continue
            candidates.append((i, j))
    need = [(i, j) for i, j in candidates if (nodes[i].trip_id, nodes[j].trip_id) not in overrides]
    dh = await estimator.estimate_many(
        [
            LegRequest(nodes[i].drop, nodes[j].pickup, day0 + timedelta(seconds=nodes[i].end_s))
            for i, j in need
        ],
        "deadhead",
    )
    estimates = dict(zip(need, dh, strict=True))
    sources: Counter[str] = Counter(n.trip_source for n in nodes)
    rejected = 0
    for i, j in candidates:
        a, b = nodes[i], nodes[j]
        override = overrides.get((a.trip_id, b.trip_id))
        if override is not None:
            edge = override
        else:
            e = estimates[(i, j)]
            p50, p80, p90, m = e.as_ints()
            sources[e.source] += 1
            buf = buffer_s(
                settings,
                policy,
                trip_p50=a.trip_p50_s,
                trip_p80=a.trip_p80_s,
                trip_p90=a.trip_p90_s,
                leg_p50=p50,
                leg_p80=p80,
                leg_p90=p90,
            )
            dh_q = p90 if quantile == "p90" else p80
            edge = Edge(p50, dh_q, p90, m, e.source, buf)
        if a.pickup_s + edge.transit_s(a) <= b.pickup_s:
            graph.edges[(i, j)] = edge
        else:
            rejected += 1

    # Hub legs, pruned to pairs a vehicle could plausibly serve.
    ff = free_flow_kmh(settings.speed_profile_json) / 3.6
    start_req: list[tuple[int, int]] = []
    end_req: list[tuple[int, int]] = []
    for v_idx, v in enumerate(vehicles):
        for n_idx, n in enumerate(nodes):
            if not v.eligible(n) or n.end_s > v.shift_end_s:
                continue
            if v.origin_s + haversine_m(v.origin, n.pickup) / ff <= n.pickup_s:
                start_req.append((v_idx, n_idx))
            if n.end_s + haversine_m(n.drop, v.end) / ff <= v.shift_end_s and n.pickup_s >= v.origin_s:
                end_req.append((n_idx, v_idx))
    starts = await estimator.estimate_many(
        [
            LegRequest(vehicles[v].origin, nodes[n].pickup, day0 + timedelta(seconds=vehicles[v].origin_s))
            for v, n in start_req
        ],
        "hub",
    )
    ends = await estimator.estimate_many(
        [LegRequest(nodes[n].drop, vehicles[v].end, day0 + timedelta(seconds=nodes[n].end_s)) for n, v in end_req],
        "hub",
    )
    for (v_idx, n_idx), e in zip(start_req, starts, strict=True):
        p50, p80, p90, m = e.as_ints()
        sources[e.source] += 1
        buf = buffer_s(settings, policy, trip_p50=0, trip_p80=0, trip_p90=0, leg_p50=p50, leg_p80=p80, leg_p90=p90)
        q = p90 if quantile == "p90" else p80
        if vehicles[v_idx].origin_s + q + buf <= nodes[n_idx].pickup_s:
            graph.start_legs[(v_idx, n_idx)] = HubLeg(p50, q, p90, m, e.source, buf)
    for (n_idx, v_idx), e in zip(end_req, ends, strict=True):
        p50, p80, p90, m = e.as_ints()
        sources[e.source] += 1
        q = p90 if quantile == "p90" else p80
        if nodes[n_idx].end_s + q <= vehicles[v_idx].shift_end_s:
            graph.end_legs[(n_idx, v_idx)] = HubLeg(p50, q, p90, m, e.source, 0)

    graph.sources = sources
    graph.stats = {
        "node_count": len(nodes),
        "vehicle_count": len(vehicles),
        "candidate_edges": len(candidates),
        "edge_count": len(graph.edges),
        "rejected_edges": rejected,
        "start_legs": len(graph.start_legs),
        "end_legs": len(graph.end_legs),
        "estimates_by_source": dict(sorted(sources.items())),
        "buffer_policy": policy,
        "quantile": quantile,
        "build_s": round(time.perf_counter() - started, 3),
    }
    return graph
