from __future__ import annotations

from datetime import date

from app.adapters.synthetic.generate import SyntheticWorld
from app.core.timeutil import to_ist

TODAY = date(2026, 10, 5)


def test_same_seed_same_rows() -> None:
    a, b = SyntheticWorld(42), SyntheticWorld(42)
    for d in (date(2026, 9, 1), date(2026, 10, 6)):
        assert [t.model_dump() for t in a.trips(d, TODAY)] == [t.model_dump() for t in b.trips(d, TODAY)]
        assert [x.model_dump() for x in a.drivers(d)] == [x.model_dump() for x in b.drivers(d)]
        assert [x.model_dump() for x in a.soc_checkins(d)] == [x.model_dump() for x in b.soc_checkins(d)]
        assert [x.model_dump() for x in a.vehicles(d)] == [x.model_dump() for x in b.vehicles(d)]


def test_different_seed_differs() -> None:
    d = date(2026, 10, 6)
    a = SyntheticWorld(1).trips(d, TODAY)
    b = SyntheticWorld(2).trips(d, TODAY)
    assert {t.trip_id for t in a}.isdisjoint({t.trip_id for t in b})


def test_fleet_and_roster_shape() -> None:
    w = SyntheticWorld(42)
    models = [v.model.model for v in w.vehicle_profiles]
    assert models.count("tigor_ev") == 12 and models.count("windsor") == 8
    assert models.count("zs_ev") == 5 and models.count("ec3") == 5
    for offset in range(14):
        d = date(2026, 10, 1 + offset)
        on = [x for x in w.drivers(d) if x.active]
        assert 25 <= len(on) <= 26
    skilled = sum(1 for p in w.driver_profiles if p.airport_skill)
    assert skilled == 18
    for account in ("A1", "A2", "A3", "A4", "A5"):
        assert sum(1 for p in w.driver_profiles if account in p.approved_accounts) == 3


def test_trip_mix_and_windows() -> None:
    w = SyntheticWorld(42)
    totals: dict[str, int] = {}
    n = 0
    for offset in range(20):
        d = date(2026, 9, 1 + offset)
        trips = w.trips(d, TODAY)
        n += len(trips)
        for t in trips:
            totals[t.trip_type] = totals.get(t.trip_type, 0) + 1
            local = to_ist(t.scheduled_pickup_at)
            minute = local.hour * 60 + local.minute
            if t.trip_type == "package":
                assert t.package_hours in (4.0, 8.0)
                assert 540 <= minute <= 840
                assert (t.pickup_lat, t.pickup_lng) == (t.drop_lat, t.drop_lng)
            if t.trip_type == "airport":
                assert 210 <= minute <= 420 or 1080 <= minute <= 1380
            if t.status == "completed":
                assert t.actual_pickup_at is not None and t.actual_drop_at is not None
                assert t.actual_drop_at > t.actual_pickup_at
    assert 100 <= n / 20 <= 140
    assert 0.28 <= totals["ets"] / n <= 0.42
    assert 0.18 <= totals["airport"] / n <= 0.32
    assert 0.05 <= totals["package"] / n <= 0.15


def test_future_days_have_no_actuals() -> None:
    w = SyntheticWorld(42)
    trips = w.trips(date(2026, 10, 7), TODAY)
    assert all(t.status in ("booked", "cancelled") for t in trips)
    assert all(t.actual_pickup_at is None for t in trips)


def test_soc_checkins_mostly_full() -> None:
    w = SyntheticWorld(42)
    socs = [c.soc_pct for off in range(30) for c in w.soc_checkins(date(2026, 9, 1 + off % 28))]
    low = [s for s in socs if s < 100]
    assert all(55 <= s <= 85 for s in low)
    assert 0.04 <= len(low) / len(socs) <= 0.18
