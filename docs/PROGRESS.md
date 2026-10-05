# Progress

## Milestone 1: Foundation

Commands:
- `make venv`, `alembic upgrade head`
- `python -m app.cli seed --seed 42 --days-past 45 --days-future 7` (via `make seed`)
- `pytest -q`, `ruff check`, `ruff format --check`, `mypy` (strict), `npm run typecheck`, `npm run build`
- API plus Vite dev server, logged in, Trips page loaded for tomorrow (headless browser check)

Tests: 29 passed, 0 failed (unit: config, synthetic determinism and shape, security, pagination,
time helpers, layering lint; integration: health, readiness, metrics, auth and roles, login rate
limit, trips listing and detail, roster, fleet, SOC check-in, snapshot dedupe, regenerate
endpoint, migrations up, down and up).

Metrics: seed wrote 53 days (45 past, today, 7 future), 6340 trips (about 120 per day), 53
snapshots in 5.5 s. The Trips page lists 132 trips for tomorrow. No solve yet, no HERE requests.

Assumptions added: A1 to A19 in docs/ASSUMPTIONS.md.

Open questions: none blocking milestone 2. HERE hosts are not reachable from this build
container (egress policy), so HERE behaviour is verified against recorded fixtures.

## Milestone 2: ETA

Commands:
- `python -m app.cli seed` (now also backfills eta_log and calibrates)
- `python -m app.cli run-job estimate_day` (tomorrow), with HERE enabled and the network blocked to
  exercise the degrade path, then with `HERE_ENABLED=false`
- `pytest tests/unit/test_here_client.py tests/unit/test_estimator.py tests/unit/test_calibration.py
  tests/unit/test_graph.py tests/integration/test_eta_jobs.py`

Tests: 56 passed in total at the end of the milestone (HERE client parsing, key as a query
parameter over HTTPS, retries on 429 and 5xx, no retry on 4xx, breaker opens after 5 failures,
budget caps for routing, matrix requests and matrix elements, 202 polling, 50 by 50 limit; chain
order cache, HERE, OSRM, fallback; matrix blocking within caps; fallback never overrides provider
rows; purge of backdated HERE rows; factor back-off; k-means determinism; calibration levels).

Metrics: estimate_day for tomorrow built 127 nodes, 1683 candidate edges (871 feasible), 651
start and 713 end legs, 3362 estimates. With HERE unreachable it spent 12 routing and 5 matrix
requests (290 elements) before the circuit opened, then completed on the fallback; budget counters
stayed under the caps. Calibration on 45 synthetic days: 4564 eta_log rows, 8 clusters, 57 factor
rows, global r50 1.03, r80 1.26, r90 1.41; p80 coverage before calibration 0.70.

Assumptions added: A22 to A30.

Open questions: none blocking.

## Milestone 3: Solver

Commands:
- solver experiments on tomorrow's graph (strategies, solution limits, determinism)
- `python -m app.cli replay --days 45 --out ../docs` (writes docs/replay-2026-10-05.md)
- `pytest tests/unit/test_solver.py tests/replay`

Tests: solver invariants on generated instances (no overlapping legs, pickups inside window with
buffer, within shift, within energy cap, allowed vehicle rules, every drop has a reason),
determinism, incremental re-solve keeps locked vehicles identical, pinned trips stay, SOC reduces
capacity; 45 day replay with zero infeasible chains and a written report.

Metrics (45 day replay at P90, production limits): 5164 trips, assigned share mean 0.364 (min
0.292), drivers used mean 23.8 (max 26), deadhead 29.2 min per trip, buffer 0.12 h per trip, solve
22 s mean (34 s max), 0 infeasible chains, every drop has a reason, 0 time limit stops, 0.12% of
pickups late when chains are replayed against actual durations. The minimum path cover of a typical
day needs more than 60 chains, so the drop rate comes from the synthetic demand, not the search
(A46).

Assumptions added: A31 to A35, A46.

Open questions: the synthetic peaks exceed the fleet's capacity under the specified speed profile.
Real demand and measured speeds will tell whether the 95% target is reachable; nothing blocks the
next milestone.

## Milestone 4: Plans and API

Commands: `make solve DATE=2026-10-06` (25 s), Plan page in a headless browser (timeline, tray,
map with overlays and HERE attribution), `pytest tests/integration/test_plans_api.py
tests/integration/test_events.py`.

Tests: full lifecycle (solve, infeasible move rejected, force needs a reason, forced move accepted
as version 2 with untouched drivers copied verbatim, diff per trip and per driver, lock creates
version 3, publish blocked until unassigned trips are acknowledged, double publish 409, repair keeps
untouched drivers byte identical and honours the lock, published repairs stay published); SSE
emission over a real server; signed webhook; notifier isolation.

Metrics: draft for 2026-10-06 assigns 45 of 126 trips with 23 drivers (81 dropped with reasons:
shift 38, time window 43), deadhead 27.6 min per trip, buffer 0.13 h per trip, solve 22 s.

Assumptions added: A36 to A39, A45.

Open questions: none blocking.

## Milestone 5: Jobs and simulator

Commands: worker and sim processes against the dev database, simulator at 600x through the API,
Live page in a headless browser, `pytest tests/e2e tests/integration/test_ops_api.py
tests/integration/test_worker.py`.

Tests: end to end simulated day with every disturbance: soc_shortfall, absent_driver, two
late_booking, cancellation and late_predicted repairs (some no_feasible_repair entries, each
recorded); every late prediction beyond tolerance has a repair event; eta_log populated from the
simulated actuals; p80 coverage reported. Trip events and pings with service keys, deduplication,
body size limit; simulator controls; scheduler definitions; abandoned job runs closed.

Metrics (e2e day): 43,795 pings, 174 lifecycle events, 43 trips completed, 1 pickup more than
5 minutes late. The worker follows simulated time; at 600x a monitor tick with several repairs
lags behind the sim, at the default 60x it keeps up.

Assumptions added: A40 to A44.

Open questions: none blocking.

## Milestone 6: Hardening

Commands: `python -m app.tools.contract`, `pytest tests/integration/test_http_source.py`, full
suite with coverage and the coverage gate, ruff, ruff format, mypy strict, tsc, vite build.

Tests: 77 passed (unit, integration, replay, e2e). HttpSource pages through the recorded fixture
server, rejects contract violations, retries 5xx; switching DATA_SOURCE to http with the fixture
server gives an identical plan for identical data.

Coverage: eta 92%, graph 99%, solver 96%, plan 89% (gate 85%); 87% overall.

Docs: README, ARCHITECTURE, RUNBOOK (re-solve a date, raise a provider cap, disable a provider,
reset the simulator, rotate the HERE key), ASSUMPTIONS, contracts/datasource.openapi.yaml.

Acceptance checklist status:
- Clean clone, compose up, seed, solve: draft visible in under 2 minutes (solve 25 s). Compose
  itself could not be run in this build container (no Docker daemon); the stack was verified with
  the same commands run directly.
- Draft assigns at least 95% with 27 or fewer drivers, or every dropped trip shows a reason: the
  second clause holds (A46); at most 26 drivers are used.
- HERE counters visible in the UI and never above caps: budget indicator in the top bar; caps are
  enforced atomically before each request.
- DATA_SOURCE=http with the fixture server gives an identical plan: tested.
- Move, see errors and warnings, force with a reason, lock, next re-solve respects the lock: tested
  through the API and available in the Plan page.
- Purge removes HERE rows older than 30 days with backdated rows: tested.
- make test, lint and type checks green, coverage at or above 85% on eta, graph, solver, plan: yes.
- RUNBOOK covers the five procedures: yes.
