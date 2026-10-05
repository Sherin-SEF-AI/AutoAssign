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
