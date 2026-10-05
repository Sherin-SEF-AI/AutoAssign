"""Day simulator: drives the published plan for a service date on a simulated clock.

Drivers start at their start location at shift start, move along each planned leg at the speed
profile for the current bin times lognormal noise (sigma 0.2), write a ping every 30 simulated
seconds, and report trip lifecycle events with actuals. Everything goes through the same paths
the driver app and booking backends use (trip events, pings, SOC check-ins).

Disturbances (each switchable): slow_legs (20% of legs take 40% longer), no_show_driver (one
driver never starts), low_soc (one vehicle reports 55% at check-in), late_bookings (two trips
booked at 10:15 and 15:40), cancellation (one trip cancels 40 minutes before pickup).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from app.adapters.base import PingRecord, SocCheckinRecord, TripEvent, TripRecord
from app.adapters.synthetic import profiles as P
from app.adapters.synthetic.generate import _jitter, _near, _uuid
from app.adapters.synthetic.zones import CITY_ZONES
from app.core.geo import Point, haversine_m, interpolate
from app.core.logging import get_logger
from app.core.timeutil import IST, ist_at, to_ist
from app.db.models import Plan, PlanAssignment
from app.db.queries import drivers_in_snapshot, plan_assignments, plan_routes, published_plan
from app.db.session import session_scope
from app.db.snapshots import latest_snapshot, load_snapshot_records, upsert_soc_checkins
from app.jobs.context import AppContext
from app.jobs.late_booking import enqueue
from app.plan.events import apply_trip_event, insert_pings

log = get_logger("sim")

DISTURBANCES = ("slow_legs", "no_show_driver", "low_soc", "late_bookings", "cancellation")
START_HOUR = 4.5
LATE_BOOKING_TIMES = ((10, 15), (15, 40))
LOW_SOC_PCT = 55.0
SLOW_SHARE = 0.20
SLOW_FACTOR = 1.40
NOISE_SIGMA = 0.2


@dataclass(slots=True)
class Leg:
    origin: Point
    dest: Point
    start: datetime
    end: datetime
    distance_m: float


@dataclass(slots=True)
class SimDriver:
    driver_id: uuid.UUID
    home: Point
    shift_start: datetime
    shift_end: datetime
    position: Point
    state: str = "off"  # off, idle, to_pickup, waiting, on_trip, absent
    trip_id: uuid.UUID | None = None
    leg: Leg | None = None
    last_ping: datetime | None = None
    done: set[uuid.UUID] = field(default_factory=set)


@dataclass(slots=True)
class SimStats:
    pings: int = 0
    events: int = 0
    completed: int = 0
    late_pickups: int = 0
    late_bookings: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    no_show_driver: str | None = None
    low_soc_vehicle: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def all_disturbances(on: bool = True) -> dict[str, bool]:
    return dict.fromkeys(DISTURBANCES, on)


class SimEngine:
    def __init__(
        self,
        ctx: AppContext,
        service_date: date,
        *,
        seed: int = 7,
        disturbances: dict[str, bool] | None = None,
        ping_interval_s: int = 30,
    ):
        self.ctx = ctx
        self.day = service_date
        self.rng = random.Random(f"sim:{seed}:{service_date}")
        self.seed = seed
        self.disturbances = {**all_disturbances(False), **(disturbances or {})}
        self.ping_interval = timedelta(seconds=ping_interval_s)
        self.now = ist_at(service_date, int(START_HOUR), int((START_HOUR % 1) * 60))
        self.drivers: dict[uuid.UUID, SimDriver] = {}
        self.trips: dict[uuid.UUID, TripRecord] = {}
        self.plan_id: uuid.UUID | None = None
        self.queues: dict[uuid.UUID, list[PlanAssignment]] = {}
        self.cancelled: set[uuid.UUID] = set()
        self.stats = SimStats()
        self.no_show: uuid.UUID | None = None
        self.low_soc_vehicle: uuid.UUID | None = None
        self.cancel_target: tuple[uuid.UUID, datetime] | None = None
        self._fired: set[str] = set()
        self._event_seq = 0

    # Setup ----------------------------------------------------------------------

    async def prepare(self) -> None:
        async with self.ctx.factory() as session:
            plan = await published_plan(session, self.day)
            if plan is None:
                raise RuntimeError(f"no published plan for {self.day}; publish one before simulating")
            drivers = await drivers_in_snapshot(session, plan.snapshot_id)
            snap = await latest_snapshot(session, self.day)
            assert snap is not None
            trips, _, _ = await load_snapshot_records(session, snap.snapshot_id)
            rows = await plan_assignments(session, plan.plan_id)
        self.trips = {t.trip_id: t for t in trips}
        for d in drivers:
            if not d.active or d.shift_start_at is None or d.shift_end_at is None:
                continue
            home = Point(d.start_lat, d.start_lng)
            self.drivers[d.driver_id] = SimDriver(d.driver_id, home, d.shift_start_at, d.shift_end_at, home)
        working = sorted({a.driver_id for a in rows if a.driver_id is not None}, key=str)
        by_driver: dict[uuid.UUID, list[PlanAssignment]] = {}
        for a in rows:
            if a.driver_id is not None:
                by_driver.setdefault(a.driver_id, []).append(a)
        if self.disturbances["no_show_driver"] and working:
            # A driver with work who starts at 05:00 or 09:00, so the miss is visible early.
            early = [
                d
                for d in working
                if d in self.drivers
                and to_ist(self.drivers[d].shift_start).hour in (5, 9)
                and len(by_driver[d]) >= 2
            ]
            self.no_show = (early or working)[self.rng.randrange(len(early or working))]
            self.stats.no_show_driver = str(self.no_show)
        if self.disturbances["low_soc"]:
            async with self.ctx.factory() as session:
                routes = sorted(
                    await plan_routes(session, plan.plan_id),
                    key=lambda r: (
                        -(r.energy_wh / r.energy_cap_wh if r.energy_cap_wh else 0),
                        str(r.vehicle_id),
                    ),
                )
            pick = next((r for r in routes if r.driver_id != self.no_show), None)
            if pick is not None:
                self.low_soc_vehicle = pick.vehicle_id
                self.stats.low_soc_vehicle = str(pick.vehicle_id)
        if self.disturbances["cancellation"]:
            candidates = sorted(
                (
                    a
                    for a in rows
                    if a.driver_id is not None
                    and a.driver_id != self.no_show
                    and a.planned_pickup_at
                    and 11 <= to_ist(a.planned_pickup_at).hour < 20
                ),
                key=lambda a: str(a.trip_id),
            )
            if candidates:
                a = candidates[self.rng.randrange(len(candidates))]
                assert a.planned_pickup_at is not None
                self.cancel_target = (a.trip_id, a.planned_pickup_at - timedelta(minutes=40))
        await self._refresh_plan(force=True)

    # Plan following -------------------------------------------------------------

    async def _refresh_plan(self, force: bool = False) -> None:
        async with self.ctx.factory() as session:
            plan: Plan | None = await published_plan(session, self.day)
            if plan is None or (not force and plan.plan_id == self.plan_id):
                return
            rows = await plan_assignments(session, plan.plan_id)
            snap = await latest_snapshot(session, self.day)
            if snap is not None:
                trips, _, _ = await load_snapshot_records(session, snap.snapshot_id)
                self.trips.update({t.trip_id: t for t in trips})
        self.plan_id = plan.plan_id
        queues: dict[uuid.UUID, list[PlanAssignment]] = {}
        for a in rows:
            if a.driver_id is not None:
                queues.setdefault(a.driver_id, []).append(a)
        for q in queues.values():
            q.sort(key=lambda r: r.seq or 0)
        self.queues = queues
        # A driver heading to a pickup that was handed to someone else stops and waits for orders.
        for drv in self.drivers.values():
            if drv.state in ("to_pickup", "waiting") and drv.trip_id is not None:
                mine = {a.trip_id for a in self.queues.get(drv.driver_id, [])}
                if drv.trip_id not in mine or drv.trip_id in self.cancelled:
                    if drv.leg is not None:
                        drv.position = self._position(drv, self.now)
                    drv.state, drv.trip_id, drv.leg = "idle", None, None

    def _next_assignment(self, drv: SimDriver) -> PlanAssignment | None:
        for a in self.queues.get(drv.driver_id, []):
            if a.trip_id in drv.done or a.trip_id in self.cancelled:
                continue
            return a
        return None

    # Movement -------------------------------------------------------------------

    def _duration_s(self, origin: Point, dest: Point, at: datetime, *, trip: bool) -> tuple[float, float]:
        distance = haversine_m(origin, dest) * P.CIRCUITY
        local = to_ist(at)
        kmh = P.speed_at_minute(local.hour * 60 + local.minute)
        secs = distance / 1000.0 / kmh * 3600.0 * self.rng.lognormvariate(0, NOISE_SIGMA)
        if self.disturbances["slow_legs"] and self.rng.random() < SLOW_SHARE:
            secs *= SLOW_FACTOR
        if trip:
            secs += P.HANDLING_S
        return secs, distance

    def _start_leg(self, drv: SimDriver, dest: Point, *, trip: bool, hours: float | None = None) -> None:
        if hours is not None:
            secs, distance = hours * 3600.0, self.rng.uniform(30_000, 90_000)
        else:
            secs, distance = self._duration_s(drv.position, dest, self.now, trip=trip)
        drv.leg = Leg(drv.position, dest, self.now, self.now + timedelta(seconds=max(30.0, secs)), distance)

    def _position(self, drv: SimDriver, at: datetime) -> Point:
        leg = drv.leg
        if leg is None:
            return drv.position
        total = (leg.end - leg.start).total_seconds()
        frac = (at - leg.start).total_seconds() / total if total > 0 else 1.0
        return interpolate(leg.origin, leg.dest, frac)

    # Events ---------------------------------------------------------------------

    def _event(self, trip_id: uuid.UUID, status: str, drv: SimDriver, **extra: Any) -> TripEvent:
        self._event_seq += 1
        return TripEvent(
            event_id=f"sim:{self.day}:{trip_id}:{status}",
            event_type="trip.status",
            occurred_at=self.now,
            trip_id=trip_id,
            service_date=self.day,
            status=status,
            driver_id=drv.driver_id,
            **extra,
        )

    async def _fire_disturbances(self, events: list[TripEvent]) -> list[TripEvent]:
        planning: list[TripEvent] = []
        if self.disturbances["low_soc"] and self.low_soc_vehicle and "low_soc" not in self._fired:
            if self.now >= ist_at(self.day, 5, 30):
                self._fired.add("low_soc")
                driver = next(
                    (
                        a.driver_id
                        for q in self.queues.values()
                        for a in q
                        if a.vehicle_id == self.low_soc_vehicle
                    ),
                    None,
                )
                async with session_scope(self.ctx.factory) as session:
                    await upsert_soc_checkins(
                        session,
                        [
                            SocCheckinRecord(
                                driver_id=driver,
                                vehicle_id=self.low_soc_vehicle,
                                service_date=self.day,
                                soc_pct=LOW_SOC_PCT,
                                odometer_km=None,
                                reported_at=self.now,
                            )
                        ],
                        source="driverapp",
                    )
        if self.disturbances["late_bookings"]:
            for n, (h, m) in enumerate(LATE_BOOKING_TIMES):
                key = f"late:{n}"
                if key not in self._fired and self.now >= ist_at(self.day, h, m):
                    self._fired.add(key)
                    trip = self._late_trip(n)
                    self.trips[trip.trip_id] = trip
                    self.stats.late_bookings.append(str(trip.trip_id))
                    planning.append(
                        TripEvent(
                            event_id=f"sim:{self.day}:late:{n}",
                            event_type="trip.created",
                            occurred_at=self.now,
                            trip=trip,
                        )
                    )
        if self.cancel_target and "cancel" not in self._fired and self.now >= self.cancel_target[1]:
            self._fired.add("cancel")
            trip_id = self.cancel_target[0]
            self.cancelled.add(trip_id)
            self.stats.cancelled.append(str(trip_id))
            planning.append(
                TripEvent(
                    event_id=f"sim:{self.day}:cancel:{trip_id}",
                    event_type="trip.cancelled",
                    occurred_at=self.now,
                    trip_id=trip_id,
                    service_date=self.day,
                )
            )
        _ = events
        return planning

    def _late_trip(self, n: int) -> TripRecord:
        rng = random.Random(f"sim-late:{self.seed}:{self.day}:{n}")
        a = rng.choice(CITY_ZONES)
        b = _near(rng, a, CITY_ZONES, 6.0)
        pickup, drop = _jitter(rng, a), _jitter(rng, b)
        local = to_ist(self.now) + timedelta(minutes=75)
        minute = (local.hour * 60 + local.minute) // 5 * 5
        scheduled = datetime(local.year, local.month, local.day, minute // 60, minute % 60, tzinfo=IST)
        return TripRecord(
            trip_id=_uuid("sim-late", self.seed, self.day, n),
            service_date=self.day,
            channel="blurabbit",
            scheduled_pickup_at=scheduled,
            pickup_lat=pickup.lat,
            pickup_lng=pickup.lng,
            pickup_address=pickup.address,
            drop_lat=drop.lat,
            drop_lng=drop.lng,
            drop_address=drop.address,
            trip_type="city",
            pax=1,
            luggage=0,
            vehicle_class="any",
            tags={"late_booking": True},
            status="booked",
            lifecycle={"booked": self.now},
        )

    # Stepping -------------------------------------------------------------------

    async def step_to(self, target: datetime) -> dict[str, Any]:
        """Advance the simulation in ping sized increments up to target."""
        steps = 0
        while self.now + self.ping_interval <= target:
            self.now += self.ping_interval
            await self._tick()
            steps += 1
        return {"sim_time": self.now.isoformat(), "steps": steps}

    async def _tick(self) -> None:
        await self._refresh_plan()
        events: list[TripEvent] = []
        pings: list[PingRecord] = []
        for drv in sorted(self.drivers.values(), key=lambda d: str(d.driver_id)):
            self._advance(drv, events)
            if drv.state not in ("off", "absent") and (
                drv.last_ping is None or self.now - drv.last_ping >= self.ping_interval
            ):
                pos = self._position(drv, self.now)
                speed = 0.0
                if drv.leg is not None and drv.state in ("to_pickup", "on_trip"):
                    secs = max(1.0, (drv.leg.end - drv.leg.start).total_seconds())
                    speed = drv.leg.distance_m / secs
                pings.append(
                    PingRecord(
                        driver_id=drv.driver_id,
                        ts=self.now,
                        lat=round(pos.lat, 6),
                        lng=round(pos.lng, 6),
                        speed_mps=round(speed, 2),
                        accuracy_m=8.0,
                    )
                )
                drv.last_ping = self.now
        planning = await self._fire_disturbances(events)
        async with session_scope(self.ctx.factory) as session:
            settings = await self.ctx.settings()
            self.stats.pings += await insert_pings(session, pings)
            for ev in events:
                await apply_trip_event(session, settings, ev)
                self.stats.events += 1
            results = [await apply_trip_event(session, settings, ev) for ev in planning]
        for r in results:
            if not r.duplicate:
                await enqueue(self.ctx.redis, r)

    def _advance(self, drv: SimDriver, events: list[TripEvent]) -> None:
        now = self.now
        if drv.state == "off":
            if drv.driver_id == self.no_show:
                if now >= drv.shift_start:
                    drv.state = "absent"
                return
            if now >= drv.shift_start:
                drv.state = "idle"
            else:
                return
        if drv.state == "absent":
            return
        if drv.state == "idle":
            nxt = self._next_assignment(drv)
            if nxt is None or nxt.planned_pickup_at is None:
                return
            depart = nxt.planned_depart_at or nxt.planned_pickup_at
            if now < depart:
                return
            trip = self.trips.get(nxt.trip_id)
            if trip is None:
                return
            drv.trip_id = nxt.trip_id
            drv.state = "to_pickup"
            self._start_leg(drv, Point(trip.pickup_lat, trip.pickup_lng), trip=False)
            events.append(self._event(nxt.trip_id, "en_route", drv))
            return
        if drv.trip_id is None:
            drv.state = "idle"
            return
        trip = self.trips[drv.trip_id]
        if drv.state == "to_pickup" and drv.leg is not None and now >= drv.leg.end:
            drv.position = drv.leg.dest
            drv.leg = None
            drv.state = "waiting"
            events.append(self._event(drv.trip_id, "arrived", drv))
        if drv.state == "waiting" and now >= trip.scheduled_pickup_at:
            if now - trip.scheduled_pickup_at > timedelta(minutes=5):
                self.stats.late_pickups += 1
            drv.state = "on_trip"
            hours = trip.package_hours if trip.trip_type == "package" else None
            self._start_leg(drv, Point(trip.drop_lat, trip.drop_lng), trip=True, hours=hours)
            events.append(self._event(drv.trip_id, "started", drv, actual_pickup_at=now))
            return
        if drv.state == "on_trip" and drv.leg is not None and now >= drv.leg.end:
            distance = drv.leg.distance_m
            drv.position = drv.leg.dest
            drv.leg = None
            events.append(
                self._event(
                    drv.trip_id, "completed", drv, actual_drop_at=now, actual_distance_m=round(distance, 1)
                )
            )
            drv.done.add(drv.trip_id)
            self.stats.completed += 1
            drv.trip_id = None
            drv.state = "idle"
