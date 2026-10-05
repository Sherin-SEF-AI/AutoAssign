from __future__ import annotations

import random

from app.core.geo import Point
from app.eta.calibration import Residual, calibrate, kmeans, nearest
from app.eta.factors import Factors, FactorTable, default_factors, quantile


def test_quantile_interpolates() -> None:
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([5], 0.9) == 5


def test_backoff_selection(settings) -> None:  # type: ignore[no-untyped-def]
    t = FactorTable(defaults=default_factors(settings))
    t.rows[("here", 2, "weekday", 3)] = Factors(1.1, 1.3, 1.5, "cell")
    t.rows[("here", 2, "weekday", None)] = Factors(1.0, 1.2, 1.4, "bin_weekday")
    t.rows[("here", 2, None, None)] = Factors(0.9, 1.1, 1.3, "bin")
    t.rows[("here", None, None, None)] = Factors(0.8, 1.0, 1.2, "global")
    assert t.lookup("here", 2, "weekday", 3).level == "cell"
    assert t.lookup("here", 2, "weekday", 5).level == "bin_weekday"
    assert t.lookup("here", 2, "weekend", 3).level == "bin"
    assert t.lookup("here", 4, "weekday", 3).level == "global"
    assert t.lookup("fallback", 4, "weekday", 3).level == "default"
    assert t.calibrated


def test_osrm_defaults_carry_congestion(settings) -> None:  # type: ignore[no-untyped-def]
    d = default_factors(settings)
    assert d["osrm"][0].r50 == 1.0  # night bin is free flow
    assert d["osrm"][4].r50 == round(38 / 13, 4)
    assert d["here"][4].r80 == 1.15


def test_calibrate_levels_respect_min_n() -> None:
    rng = random.Random(1)
    res = [Residual("fallback", 2, "weekday", 0, rng.lognormvariate(0, 0.25)) for _ in range(40)]
    res += [Residual("fallback", 2, "weekday", 1, rng.lognormvariate(0, 0.25)) for _ in range(10)]
    out = calibrate(res, 30)
    levels = {(f.level, f.zone_cluster) for f in out}
    assert ("cell", 0) in levels and ("cell", 1) not in levels
    assert ("bin_weekday", None) in levels and ("global", None) in levels
    for f in out:
        assert f.r50 <= f.r80 <= f.r90 and f.n >= 30


def test_kmeans_deterministic_and_nearest() -> None:
    pts = [(Point(12.9 + (i % 5) * 0.05, 77.5 + (i // 5) * 0.05), 1 + i % 3) for i in range(25)]
    a = kmeans(pts, 4, seed=0)
    b = kmeans(list(reversed(pts)), 4, seed=0)
    assert a == b and len(a) == 4
    assert nearest(a, a[2]) == 2
    assert nearest([], Point(0, 0)) is None
