# Runbook

Operational guide for blurabbit-dispatch. Commands assume the repository root and Docker
Compose; add `LOCAL=1` to Make targets to use `backend/.venv` instead of containers.

## Daily timeline (Asia/Kolkata)

| Time | Job | What it does |
| --- | --- | --- |
| 02:00 Sun | calibrate_factors | Rebuilds zone clusters and the factor table from eta_log |
| 03:00 | purge_here_cache | Deletes HERE rows past 30 days, nulls old HERE predictions, keeps ping partitions ahead |
| 06:00 | morning_validate | Pulls SOC check-ins, repairs vehicles below plan, marks absent drivers |
| 06:00 to 23:30, every 5 min | intraday_monitor | Slack per driver, HERE for at-risk legs, late_predicted repairs, absence checks for later shifts |
| hourly at :07 | eta_actuals | Joins actual pickup and drop times to estimates into eta_log |
| 19:45 | ingest | Pulls tomorrow's trips, roster, vehicles and hubs into a snapshot |
| 20:00 | nightly_solve | Graph, estimates, OR-Tools solve, draft version 1, plan.drafted |
| 21:30 | auto_publish | Publishes the latest draft unless ops overrode it since 20:00 |
| on event | late_booking | trip.created, trip.updated, trip.cancelled become incremental repairs |

Every run writes one `job_run` row (Jobs page, `GET /api/v1/jobs/runs`). A rerun for the same
date is a no-op unless the snapshot changed.

## Alerts and what they mean

All metrics are at `GET /metrics` (api) and `:9100/metrics` (worker).

| Condition | Meaning | Action |
| --- | --- | --- |
| `eta_fallback_total` rising fast | HERE and OSRM are unavailable or over budget; plans use the haversine fallback | Check `GET /readyz` provider circuits and `GET /api/v1/admin/budget`; see "Raise a provider cap" or "Disable a provider" |
| `budget_remaining{provider}` at 0 | A daily HERE cap was reached; HERE is skipped until midnight IST | Expected late in heavy days. Raise the cap only if the contract allows |
| readiness shows a circuit `open` | 5 consecutive failures; the provider is skipped for 10 minutes | Check HERE status and the key; the circuit half-opens by itself |
| `plan_unassigned_trips` above normal | The draft dropped more trips than usual | Open the Plan page tray; reasons explain each drop (shift, time_window, energy_cap, no_eligible_vehicle) |
| `monitor_at_risk_drivers` high | Many drivers predicted to have under 15 minutes of slack | Live page; consider manual re-solves of the red drivers |
| `repair_events_total{trigger}` with outcome no_feasible_repair | A repair found no better plan; the trip stays with its driver, predicted late | Call the customer or reassign by hand on the Plan page |
| `eta_p80_coverage` far from 0.80 | Calibration drift | Run calibrate_factors; investigate if it persists |
| `job_runs_total{status="failed"}` | A job raised | Jobs page shows the error text; rerun with the Run button |
| `solve_duration_seconds` near SOLVER_TIME_LIMIT_S | Search stopped on time, not on the solution limit, so results may vary between machines | Lower SOLVER_SOLUTION_LIMIT or raise the time limit |

## Re-solve a date

1. Plan page, pick the date, "Solve date". Or `make solve DATE=2026-10-07`.
   The API equivalent is `POST /api/v1/plans/solve {"service_date": "2026-10-07"}`.
2. The new draft is a new version with the previous one as parent; nothing is overwritten.
3. Review the diff toggle against the published version, acknowledge unassigned trips, publish.

To re-solve only some drivers (keeps everyone else byte identical):
`POST /api/v1/plans/{plan_id}/resolve {"driver_ids": [...], "reason": "..."}` or the Re-solve
button on the Live page.

## Raise a provider cap

Caps are runtime settings; no restart needed.

1. Settings page, Caps group, change `here_routing_daily_cap`, `here_matrix_daily_cap` or
   `here_matrix_elements_daily_cap`, Save (admin role). The change is audited.
2. Or `PUT /api/v1/settings {"here_routing_daily_cap": 800}`.
3. Counters are per IST day in Redis (`budget:{provider}:{yyyymmdd}`); raising a cap takes effect
   on the next request. Lowering a cap below today's usage stops HERE for the rest of the day.

## Disable a provider

1. Settings page, Providers group: `here_enabled` or `osrm_enabled` off. Or
   `PUT /api/v1/settings {"here_enabled": false}`.
2. The estimator degrades to the next provider; solves still complete. Map tiles stop while HERE is
   off (the map shows its overlays and a note).
3. To skip HERE permanently, also unset `HERE_API_KEY` in `.env` and restart api and worker.

## Reset the simulator

1. Live page, simulator box, Reset (admin), or `POST /api/v1/admin/sim/reset`.
2. This removes the simulated clock (all jobs return to wall time) and the sim process clears
   pings, live trip states and monitor state for the simulated date. Plan versions and repair
   events stay, because history is immutable.
3. Start again with `POST /api/v1/admin/sim/start {"service_date": "...", "speed": 60}`. The sim
   service must run: `docker compose --profile sim up -d sim` (`make sim`).

## Rotate the HERE key

1. Create the new key in the HERE platform; keep the old one active.
2. Set `HERE_API_KEY` in `.env` to the new key and restart: `docker compose up -d api worker`.
3. Check `GET /readyz` (circuits closed) and the Plan page map tiles.
4. Revoke the old key in the HERE platform. Cached estimates do not depend on the key.

## Switch to the BluRabbit backends

Set `DATA_SOURCE=http`, `UPSTREAM_USERAPP_URL`, `UPSTREAM_DRIVERAPP_URL`, `UPSTREAM_ADMIN_URL`,
`UPSTREAM_API_KEY`, and `NOTIFY_WEBHOOK_URL` with `NOTIFY_WEBHOOK_SECRET`; restart. The contract is
`contracts/datasource.openapi.yaml`. Nothing else changes.

## Database and data

- Migrations: `make migrate` (runs automatically through the `migrate` compose service).
- Regenerate synthetic data: Data page or `POST /api/v1/admin/synthetic/regenerate`; this wipes
  operational tables (keeps users, settings and job history).
- Backups: standard Postgres `pg_dump`; Redis holds only caches, counters, locks and the sim clock.
