# BluRabbit Dispatch. Targets run inside Docker by default; pass LOCAL=1 to use backend/.venv.
SHELL := /bin/bash
SEED ?= 42
DAYS_PAST ?= 45
DAYS_FUTURE ?= 7
DAYS ?= 45
DATE ?=
COMPOSE ?= docker compose

ifeq ($(LOCAL),1)
PY := cd backend && .venv/bin/python
ALEMBIC := cd backend && .venv/bin/alembic
else
PY := $(COMPOSE) run --rm api python
ALEMBIC := $(COMPOSE) run --rm api alembic
endif

.PHONY: up down logs migrate seed solve sim sim-stop replay test test-unit test-integration lint typecheck \
	web-build openapi venv osrm-data

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) --profile sim --profile osrm down

logs:
	$(COMPOSE) logs -f --tail=200 api worker

migrate:
	$(ALEMBIC) upgrade head

seed:
	$(PY) -m app.cli seed --seed $(SEED) --days-past $(DAYS_PAST) --days-future $(DAYS_FUTURE)

solve:
	$(PY) -m app.cli solve $(if $(DATE),--date $(DATE),)

sim:
	$(COMPOSE) --profile sim up -d --build sim

sim-stop:
	$(COMPOSE) --profile sim stop sim

replay:
ifeq ($(LOCAL),1)
	$(PY) -m app.cli replay --days $(DAYS) --out ../docs
else
	$(COMPOSE) run --rm --user root worker python -m app.cli replay --days $(DAYS) --out /app/reports
endif

venv:
	cd backend && uv venv -p 3.12 .venv && uv pip install -p .venv -e ".[dev]"

test:
	cd backend && .venv/bin/python -m pytest -q --cov=app --cov-report=term-missing:skip-covered
	cd web && npm run typecheck

test-unit:
	cd backend && .venv/bin/python -m pytest -q tests/unit

test-integration:
	cd backend && .venv/bin/python -m pytest -q tests/integration

lint:
	cd backend && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests && .venv/bin/mypy
	cd web && npm run typecheck

typecheck: lint

openapi:
	cd backend && .venv/bin/python -m app.export_openapi ../web/src/api/openapi.json
	cd web && npm run gen:api

web-build:
	cd web && npm ci && npm run build

osrm-data:
	mkdir -p osrm-data
	curl -L -o osrm-data/southern-zone-latest.osm.pbf https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf
	docker run --rm -v $(PWD)/osrm-data:/data ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-extract -p /opt/car.lua /data/southern-zone-latest.osm.pbf
	docker run --rm -v $(PWD)/osrm-data:/data ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-partition /data/southern-zone-latest.osrm
	docker run --rm -v $(PWD)/osrm-data:/data ghcr.io/project-osrm/osrm-backend:v5.27.1 osrm-customize /data/southern-zone-latest.osrm
