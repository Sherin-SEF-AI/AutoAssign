# Architecture

blurabbit-dispatch assigns BluRabbit's pre-booked trips to drivers a day ahead, validates the
plan each morning and repairs it during the day.

## Runtime components

| Component | Process | Role |
| --- | --- | --- |
| web | nginx serving the React SPA | Plan board, live view, fleet, data, settings, jobs; proxies `/api` to api |
| api | `uvicorn app.main:create_app --factory` | REST, SSE, auth, map tile proxy, service ingestion |
| worker | `python -m app.worker` | APScheduler jobs on Asia/Kolkata time, trip event consumer, sim ticker, metrics on :9100 |
| sim | `python -m app.sim` (profile `sim`) | Simulated clock, driver movement, pings, disturbances |
| osrm | OSRM backend (profile `osrm`) | Free-flow tables and routes |
| postgres 16, redis 7 | | Storage; Redis holds caches, budgets, locks, the event bus and the sim clock |

## Backend layering

Lower layers never import higher ones; `tests/unit/test_layering.py` enforces it.

```
config, core, db, adapters.base      base: settings, time, geo, logging, metrics, models, records
adapters (synthetic, http, here, osrm, notify)
eta        LegEstimator, provider chain, cache, factor table, calibration
graph      trip nodes, vehicle contexts, candidate edges, hub legs, buffers
solver     OR-Tools model, route evaluation, extraction, unassigned reasons
plan       versions, moves and feasibility, locks, publish, diff, repairs, read models, events
jobs, api, replay
main, worker, sim, cli, runtime      entrypoints
```

Only `adapters/synthetic`, the adapter factory and the simulator know the data is synthetic.
`DATA_SOURCE=http` swaps in `HttpSource` (contract in `contracts/datasource.openapi.yaml`).

## Nightly flow

1. `ingest` (19:45): `DataSource.list_trips(tomorrow)`, roster, vehicles, hubs, ops fleet
   exceptions applied, written to snapshot tables with a content hash (identical content reuses the
   snapshot id).
2. `nightly_solve` (20:00): plannable trips get trip-leg estimates; candidate edges (gap within
   MAX_GAP_S, straight line within MAX_DEADHEAD_KM) get deadhead estimates in batches; hub legs per
   driver; buffers per the active policy; OR-Tools solves; draft version 1 is written with solver
   params, stats and the snapshot id; `plan.drafted` goes out.
3. `auto_publish` (21:30) publishes unless ops overrode the date since 20:00.

## Estimation

`LegEstimator.estimate` and `estimate_many` serve every caller with p50, p80, p90, distance and
source. Order: Redis hot key, then the `leg_estimate` row (fresh for 24 h), HERE Routing v8
(trip legs, singles) or Matrix v8 (deadhead and hub batches, at most 50 by 50 blocks of wanted
pairs per 15 minute departure bin), OSRM, then the haversine fallback. A daily budget per HERE
provider lives in Redis and is consumed atomically before each call; a Redis backed circuit breaker
opens for 10 minutes after 5 consecutive failures. A solve never fails because a provider is out.

Provider durations are scaled by quantile factors from `factor_table` for the departure time bin,
weekday type and zone cluster with back-off to coarser levels (n of at least 30). Factors are
calibrated per basis (here, osrm, fallback) as ratios of actual to the raw provider duration; the
OSRM basis carries the congestion factor. Before calibration the buffer is
`BUFFER_FIXED_S + BUFFER_PCT * (trip_p80 + deadhead_p80)`; after, `max(BUFFER_MIN_S, p90 - p80)`
of the trip and deadhead legs.

## Solver model

* Vehicles are driver and vehicle pairs on shift (Uber shifts and vehicles in service excluded);
  each has its own start and end node (driver start and end locations), sorted by driver id.
* Nodes are trips sorted by trip id. Time dimension in seconds from 00:00 IST: a trip node's cumul
  is fixed to its scheduled pickup; transit A to B is `service_A + deadhead_p80 + buffer`; start
  and end windows are the shift; span is capped at max hours. Infeasible arcs are removed up front.
* Energy dimension in Wh per vehicle, capacity `planning_cap_km * e_roll * soc_fraction`.
* Count dimension with a soft upper bound of MAX_TRIPS_PER_DRIVER.
* Every trip is in a disjunction with DROP_PENALTY_S; dropped trips are re-checked against every
  vehicle to give a reason (no_eligible_vehicle, shift, energy_cap, time_window).
* PATH_CHEAPEST_ARC then GUIDED_LOCAL_SEARCH, single thread. The search stops on a solution limit
  (deterministic) with the time limit as a safety net; `solver_stats.stop_reason` records which.

## Plans

A plan is an append-only version chain per service date. Overrides, locks, late bookings,
cancellations and repairs create new versions with a parent and a trigger. Drivers not touched by a
change are copied verbatim. Publishing flips statuses only; a partial unique index guarantees one
published version per date. The Notifier sends `plan.published` with each driver's ordered stops
(pickup time and addresses) to the log, SSE and an HMAC signed webhook.

## Intraday

* `morning_validate` compares SOC check-ins with planned energy and repairs shortfalls; drivers
  with work and no ping by shift start plus 15 minutes are absent and their trips are repaired.
* `intraday_monitor` uses the latest ping: remaining trip leg (OSRM or fallback), stored deadhead,
  slack against the next pickup; below RISK_THRESHOLD_S one live HERE call; beyond
  LATE_TOLERANCE_S an incremental repair frees the driver and float drivers.
* Repairs freeze trips under way, seed OR-Tools from the current plan
  (`ReadAssignmentFromRoutes`), lock untouched drivers (`ApplyLocksToAllVehicles`) and pin ops
  locks. If the seed cannot be read the model is rebuilt over the freed drivers only.

## Simulator

The sim process follows the Redis clock state set through `/admin/sim/*` and drives the published
plan: drivers start at shift start, move along legs at the bin speed times lognormal noise, ping
every 30 simulated seconds and report trip lifecycle events through the same code paths as the
driver app. All jobs read time from a Clock abstraction, so the worker runs the monitor against
simulated time while a simulation runs.

## HERE terms in code

* HERE derived `leg_estimate` rows carry `expires_at = created_at + 30 days`; `purge_here_cache`
  deletes them and nulls HERE predictions in `eta_log` older than 30 days (actuals kept).
* The map uses HERE vector tiles through `/api/v1/map/tiles/...`, so routing results are only
  drawn on a HERE basemap; the key is injected server side; HERE attribution is always visible.
