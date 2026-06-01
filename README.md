# RealViewChat

Runs OpenAI Vision over property-inspection photos (kitchens & bathrooms),
stores the structured results in PostgreSQL, and serves them to a React review
dashboard. Originally a prototype, now productionised: a relational data layer,
an automated test suite with a CI quality gate, Prometheus/Grafana
observability, and an asynchronous Vision worker behind RabbitMQ.

## Architecture

```
                         ┌──────────────┐      ┌───────────────────────────┐
  React dashboard  ───▶  │  Flask REST  │ ───▶ │  PostgreSQL (7-table 3NF) │
  (web/frontend)         │ (web/backend)│      │  Alembic migrations       │
                         └──────┬───────┘      └─────────────▲─────────────┘
                                │ POST /api/inspections (202)              │
                                ▼                                          │
                         ┌──────────────┐   consume    ┌──────────────────┴──┐
                         │   RabbitMQ   │ ───────────▶ │   Vision worker      │
                         │  work + DLQ  │   retry/DLQ  │  (pipeline + persist)│
                         └──────────────┘              └──────────┬───────────┘
                                                                  │ LLMClient seam
                                                                  ▼
                                                            OpenAI Vision API

  Prometheus scrapes /metrics on the API (:5001) and the worker (:9101);
  Grafana renders the dashboard (observability/).
```

- **REST API** (`web/backend/app.py`) — Flask over PostgreSQL via a SQLAlchemy 2.0
  serializer layer that keeps responses byte-identical to the prototype.
- **Vision pipeline** (`src/realview_chat/pipeline`) — pass1 room classification →
  pass2 per-image features/scores → pass2.5 room consolidation, behind an
  injectable `LLMClient` seam (faked in tests, never calling the live API).
- **Async worker** (`src/realview_chat/messaging`) — consumes inspection jobs
  from RabbitMQ, runs the pipeline, and persists the Property aggregate in one
  transaction with bounded retry + dead-letter.
- **Observability** (`observability/`) — `/metrics` on the API and the worker,
  scraped by Prometheus, visualised by Grafana (all provisioned as code).

## Prerequisites

- Python **3.14** (project runs on 3.11–3.14)
- Node **20+**
- Docker (for PostgreSQL and RabbitMQ)
- An OpenAI API key (only needed to actually run the Vision pipeline)

## Setup

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on *nix
pip install -r requirements.txt
cd web/frontend && npm install && cd ../..
```

Create a `.env` in the project root:

```
OPENAI_API_KEY=sk-your-key-here
DATABASE_URL=postgresql+psycopg2://realview:realview_dev@localhost:5432/realview
```

Start PostgreSQL and build the schema from the migrations:

```bash
docker run -d --name realview-postgres \
  -e POSTGRES_USER=realview -e POSTGRES_PASSWORD=realview_dev -e POSTGRES_DB=realview \
  -p 5432:5432 postgres:16
alembic upgrade head     # creates the 7 tables + trigger + view + indexes
```

## Data

Put property images in `cases/case_<property_id>/` (e.g. `cases/case_2203177/`).
Get inspection results into the database one of two ways:

```bash
# A) run the pipeline to JSON, then migrate into PostgreSQL
python scripts/run_pipeline.py            # all unprocessed cases (or pass one id)
python scripts/migrate_json_to_db.py      # out/results_*.json -> PostgreSQL

# B) enqueue an async job (see "Async worker" below)
```

## Run the web app

```bash
# terminal 1 — backend (port 5001)
python web/backend/app.py

# terminal 2 — frontend (port 5173)
cd web/frontend && npm run dev
```

Open http://localhost:5173 — pick a property, review images, classify
(correct/FP/FN), and score condition/modernity/material/functionality.

Key endpoints: `GET /api/properties`, `GET /api/properties/<id>` (404 on miss),
`GET /api/properties/flagged`, `GET /api/summary`, `GET /api/stats`,
`GET|POST /api/feedback`, `POST /api/inspections` (async), `GET /metrics`.

## Async worker (RabbitMQ)

The heavy Vision pipeline runs off the request path. `POST /api/inspections`
returns **202 Accepted** and a worker consumes the job, runs the pipeline, and
persists the result — with bounded retry then dead-letter, and idempotent
persistence (a redelivered job never double-persists).

```bash
docker compose -f messaging/docker-compose.yml up -d --build   # broker + worker
curl -X POST localhost:5001/api/inspections \
     -H 'content-type: application/json' -d '{"property_id":"2203177"}'
```

Full walkthrough (incl. forcing a failure into the dead-letter queue):
[`messaging/REPRODUCE.md`](messaging/REPRODUCE.md).

## Observability

```bash
docker compose -f observability/docker-compose.yml up -d   # Prometheus + Grafana
```

- Prometheus: http://localhost:9090 — scrapes the API (`:5001/metrics`) and the
  worker (`:9101/metrics`).
- Grafana: http://localhost:3000 → **RealView – Backend Overview** (request rate,
  p95 latency, error rate, flagged-property gauge, feedback counter, async
  pipeline runs by outcome, retries & dead-lettered).

## Tests & CI

```bash
pytest                      # backend: unit + integration on a REAL Postgres test DB
cd web/frontend && npm test # frontend: Vitest
```

- Backend tests build the schema via `alembic upgrade head` and use per-test
  transaction rollback; the async tests run against a **real RabbitMQ**.
- A per-package coverage gate fails the build under 75%.
- **GitHub Actions** (`.github/workflows/ci.yml`) runs lint + pytest (with
  Postgres **and** RabbitMQ service containers) + Vitest on every push and PR.

## Project layout

```
src/realview_chat/
  db/            SQLAlchemy models, serializers, atomic persistence (ingest.py)
  pipeline/      pass1 / pass2 / pass2.5 + property_processor (LLMClient seam)
  messaging/     RabbitMQ producer, worker, topology, metrics
  openai_client/ OpenAI Vision adapter behind the LLMClient seam
web/backend/     Flask REST API (+ /metrics)
web/frontend/    React + Vite dashboard
alembic/         migrations (schema, trigger, view, indexes)
scripts/         run_pipeline, migrate_json_to_db, run_worker
observability/   Prometheus + Grafana as code
messaging/       broker + worker compose, REPRODUCE.md
tests/           pytest unit + integration (Postgres + RabbitMQ)
```

## Troubleshooting

- **No properties** — load data first (`migrate_json_to_db.py`, the pipeline, or
  an async job) so the database isn't empty.
- **DB connection errors** — confirm the `realview-postgres` container is running
  and `DATABASE_URL` in `.env` matches; run `alembic upgrade head`.
- **`/api/inspections` returns 503** — the broker isn't reachable; start it with
  `docker compose -f messaging/docker-compose.yml up -d`.
- **Port 5000 vs 5001** — the backend uses **5001** (macOS AirPlay grabs 5000).
