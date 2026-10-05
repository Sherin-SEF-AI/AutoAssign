"""Prometheus metrics, one registry per process."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

SOLVE_DURATION = Histogram(
    "solve_duration_seconds",
    "Wall time of a solver run",
    ["mode"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
PLAN_UNASSIGNED = Gauge("plan_unassigned_trips", "Unassigned trips in the latest plan", ["service_date"])
PLAN_DEADHEAD_PER_TRIP = Gauge(
    "plan_deadhead_seconds_per_trip", "Deadhead seconds per assigned trip", ["service_date"]
)
PLAN_BUFFER_PER_TRIP = Gauge(
    "plan_buffer_seconds_per_trip", "Buffer seconds per assigned trip", ["service_date"]
)
ETA_REQUESTS = Counter("eta_requests_total", "Leg estimates served", ["provider", "outcome"])
ETA_FALLBACK = Counter("eta_fallback_total", "Leg estimates served by the haversine fallback")
BUDGET_REMAINING = Gauge("budget_remaining", "Remaining daily requests per provider budget", ["provider"])
MONITOR_AT_RISK = Gauge("monitor_at_risk_drivers", "Drivers at risk in the last monitor tick")
REPAIR_EVENTS = Counter("repair_events_total", "Repair events written", ["trigger"])
ETA_P80_COVERAGE = Gauge("eta_p80_coverage", "Share of actual durations at or under predicted p80")
JOB_RUNS = Counter("job_runs_total", "Scheduled job executions", ["name", "status"])
HTTP_REQUESTS = Counter("http_requests_total", "API requests", ["method", "route", "status"])
PROVIDER_ERRORS = Counter("provider_errors_total", "External provider call failures", ["provider", "kind"])
