"""Route evaluation shared by solver extraction, move feasibility and the intraday monitor.

Given a vehicle and an ordered list of trip nodes, computes planned times, deadheads, buffers,
cumulative energy and every constraint violation. All times are seconds from 00:00 IST.

Model conventions (see docs/ASSUMPTIONS.md):
  service starts exactly at the scheduled pickup;
  the driver aims to arrive EARLY_ARRIVAL_S before pickup when time allows;
  arrival + buffer must not be later than the scheduled pickup.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.graph.model import Edge, HubLeg, TripNode, VehicleCtx

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: Severity
    message: str
    trip_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Stop:
    node: int
    trip_id: uuid.UUID
    seq: int
    depart_s: int
    arrive_pickup_s: int
    pickup_s: int
    drop_s: int
    deadhead_s: int
    deadhead_m: int
    deadhead_source: str
    buffer_s: int
    trip_s: int
    trip_m: int
    slack_s: int
    energy_wh: int


@dataclass(slots=True)
class RouteEval:
    vehicle: int
    stops: list[Stop] = field(default_factory=list)
    end_leg_s: int = 0
    end_leg_m: int = 0
    end_s: int | None = None
    start_s: int | None = None
    energy_wh: int = 0
    distance_m: int = 0
    issues: list[Issue] = field(default_factory=list)

    @property
    def feasible(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]


EdgeFn = Callable[[int, int], Edge | None]
StartFn = Callable[[int, int], HubLeg | None]
EndFn = Callable[[int, int], HubLeg | None]

SLACK_WARN_S = 600
ENERGY_WARN_SHARE = 0.85


def leg_energy_wh(settings: Settings, v: VehicleCtx, metres: int, seconds: int) -> int:
    return round(v.e_roll_wh_per_km * metres / 1000.0 + settings.p_aux_w * seconds / 3600.0)


def evaluate_route(
    settings: Settings,
    nodes: Sequence[TripNode],
    vehicles: Sequence[VehicleCtx],
    v_idx: int,
    seq: Sequence[int],
    *,
    edge: EdgeFn,
    start_leg: StartFn,
    end_leg: EndFn,
    check_eligibility: bool = True,
) -> RouteEval:
    v = vehicles[v_idx]
    out = RouteEval(vehicle=v_idx)
    cap = v.energy_cap_wh - v.energy_used_wh
    energy = 0
    distance = 0
    free_s = v.origin_s
    prev: int | None = None
    for k, n_idx in enumerate(seq):
        n = nodes[n_idx]
        if check_eligibility:
            if n.vehicle_class == "suv" and v.vehicle_class != "suv":
                out.issues.append(Issue("vehicle_class", "error", "trip needs an SUV", n.trip_id))
            if n.needs_airport_skill and "airport" not in v.skills:
                out.issues.append(Issue("skill", "error", "driver lacks the airport skill", n.trip_id))
            if n.account_id and n.account_id not in v.approved_accounts:
                out.issues.append(
                    Issue(
                        "account_approval",
                        "error",
                        f"driver not approved for account {n.account_id}",
                        n.trip_id,
                    )
                )
        if prev is None:
            leg = start_leg(v_idx, n_idx)
            if leg is None:
                out.issues.append(
                    Issue("missing_leg", "error", "no estimate from the start location", n.trip_id)
                )
                dh_s, dh_m, buf, src = 0, 0, 0, "missing"
            else:
                dh_s, dh_m, buf, src = leg.p80_s, leg.m, leg.buffer_s, leg.source
        else:
            e = edge(prev, n_idx)
            if e is None:
                out.issues.append(
                    Issue("missing_leg", "error", "no deadhead estimate between trips", n.trip_id)
                )
                dh_s, dh_m, buf, src = 0, 0, 0, "missing"
            else:
                dh_s, dh_m, buf, src = e.deadhead_p80_s, e.deadhead_m, e.buffer_s, e.source
        earliest_arrival = free_s + dh_s
        slack = n.pickup_s - earliest_arrival
        if earliest_arrival + buf > n.pickup_s:
            late = earliest_arrival + buf - n.pickup_s
            out.issues.append(
                Issue(
                    "time_window",
                    "error",
                    f"arrives {late // 60} min too late for the pickup (including buffer)",
                    n.trip_id,
                )
            )
        elif slack < SLACK_WARN_S:
            out.issues.append(Issue("low_slack", "warning", f"only {slack // 60} min of slack", n.trip_id))
        if prev is None and n.pickup_s - buf - dh_s < v.shift_start_s and v.available_from_s is None:
            out.issues.append(
                Issue("shift", "error", "first pickup cannot be reached after shift start", n.trip_id)
            )
        arrive = max(earliest_arrival, n.pickup_s - settings.early_arrival_s)
        depart = arrive - dh_s
        energy += leg_energy_wh(settings, v, dh_m + n.trip_m, dh_s + n.service_s)
        distance += dh_m + n.trip_m
        out.stops.append(
            Stop(
                node=n_idx,
                trip_id=n.trip_id,
                seq=k,
                depart_s=depart,
                arrive_pickup_s=arrive,
                pickup_s=n.pickup_s,
                drop_s=n.end_s,
                deadhead_s=dh_s,
                deadhead_m=dh_m,
                deadhead_source=src,
                buffer_s=buf,
                trip_s=n.service_s,
                trip_m=n.trip_m,
                slack_s=slack,
                energy_wh=energy,
            )
        )
        free_s = n.end_s
        prev = n_idx
    if out.stops:
        assert prev is not None
        out.start_s = out.stops[0].depart_s
        leg = end_leg(prev, v_idx)
        if leg is None:
            out.issues.append(
                Issue(
                    "shift",
                    "error",
                    "cannot return to the end location before shift end",
                    nodes[prev].trip_id,
                )
            )
        else:
            out.end_leg_s, out.end_leg_m = leg.p80_s, leg.m
            energy += leg_energy_wh(settings, v, leg.m, leg.p80_s)
            distance += leg.m
        out.end_s = free_s + out.end_leg_s
        if out.end_s > v.shift_end_s:
            out.issues.append(Issue("shift", "error", "route ends after shift end", nodes[prev].trip_id))
        if out.start_s is not None and out.end_s - out.start_s > v.max_hours * 3600:
            out.issues.append(Issue("max_hours", "error", f"route exceeds {v.max_hours:g} working hours"))
    out.energy_wh = energy
    out.distance_m = distance
    if energy > cap:
        out.issues.append(Issue("energy_cap", "error", f"needs {energy} Wh, cap is {cap} Wh"))
    elif cap > 0 and energy > ENERGY_WARN_SHARE * cap:
        out.issues.append(
            Issue("energy_high", "warning", f"uses {round(100 * energy / cap)}% of the energy cap")
        )
    if len(seq) > settings.max_trips_per_driver:
        out.issues.append(
            Issue(
                "fairness",
                "warning",
                f"{len(seq)} trips exceeds the fairness bound {settings.max_trips_per_driver}",
            )
        )
    return out
