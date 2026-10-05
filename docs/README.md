# blurabbit-dispatch

Internal web application that auto-assigns BluRabbit's pre-booked trips to drivers one day ahead,
validates the plan each morning against SOC check-ins and absentees, and repairs it during the day
from live positions. Routing and traffic come from HERE Routing v8 and Matrix Routing v8; all
internal data comes through a DataSource adapter (synthetic by default, BluRabbit backends later).

## Quick start

```bash
cp .env.example .env            # set JWT_SECRET, ADMIN_PASSWORD, SERVICE_API_KEYS, HERE_API_KEY
docker compose up -d --build    # postgres, redis, migrate, api, worker, web
make seed                       # 45 past days with actuals, today, 7 future days, calibration
make solve DATE=$(date -d tomorrow +%F)
open http://localhost:5173      # log in with ADMIN_EMAIL / ADMIN_PASSWORD
```

Optional profiles:

```bash
make osrm-data && docker compose --profile osrm up -d osrm   # then OSRM_URL=http://osrm:5000
make sim                                                     # day simulator; control it from the Live page
```

## Pages

| Page | What it is for |
| --- | --- |
| Plan | Per-driver timeline (05:00 to 24:00 IST), drag to move with feasibility, force with reason, lock, publish, diff between versions, unassigned tray with reasons, HERE map |
| Live | Same timeline with a now line, positions, slack and risk per driver, repair feed, run the monitor, re-solve a driver, simulator controls |
| Trips | Searchable trips with status, planned pickup, estimates and sources; detail drawer with estimate history and actuals |
| Fleet | Drivers and vehicles, SOC and odometer check-in |
| Data | Synthetic dataset controls, snapshots, estimates by source, replay runner |
| Settings | Buffers, caps, thresholds, provider toggles, factor table |
| Jobs | Job runs with status, duration, stats and errors; manual runs |

## Development

```bash
make venv                      # backend/.venv with dev dependencies (Python 3.12)
cd web && npm ci
make test                      # pytest with coverage, web typecheck (needs Postgres and Redis)
make lint                      # ruff, ruff format, mypy strict, tsc
make replay DAYS=45 LOCAL=1    # writes docs/replay-<date>.md
make openapi                   # re-export the OpenAPI document and regenerate the web client
```

Tests use `backend/.env.test` (database `dispatch_test`, Redis db 15). HERE calls in tests are
replayed from fixtures through respx; CI never calls HERE.

## Documents

* [ARCHITECTURE.md](ARCHITECTURE.md): components, layering, flows, solver model.
* [RUNBOOK.md](RUNBOOK.md): schedules, alerts, re-solve, caps, providers, simulator, key rotation.
* [ASSUMPTIONS.md](ASSUMPTIONS.md): every assumption made while building.
* [PROGRESS.md](PROGRESS.md): milestone reports.
* `replay-<date>.md`: go or no-go evidence from the replay harness.
* `../contracts/datasource.openapi.yaml`: what the BluRabbit backends expose for `DATA_SOURCE=http`.
