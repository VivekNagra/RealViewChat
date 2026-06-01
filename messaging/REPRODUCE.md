# Async Vision worker — reproduce

The pipeline now runs asynchronously behind RabbitMQ: a request is enqueued and a
worker consumes it, runs the Vision pipeline, and persists the Property
aggregate. Bounded retry + dead-letter give robustness under at-least-once
delivery. Worker pipeline metrics are scraped by the existing Prometheus/Grafana.

## Topology
- exchange `realview.inspections` (direct) → queue `realview.inspections.work`
  (routing key `inspect`).
- the work queue dead-letters (on nack) to exchange `realview.inspections.dlx`
  → queue `realview.inspections.dlq`.
- retry: up to **3** retries (header `x-retry-count`, immediate, no backoff),
  then the job is dead-lettered. Idempotency: the external `property_id` is
  UNIQUE, and the worker skips the pipeline+insert if the property already
  exists, so a redelivery never double-persists.

## Run the stack (two commands)
```bash
# 1) broker + worker container (worker reaches Postgres on the host)
docker compose -f messaging/docker-compose.yml up -d --build

# 2) monitoring (Prometheus scrapes the worker on :9101, Grafana dashboard)
docker compose -f observability/docker-compose.yml up -d
```
RabbitMQ management UI: http://localhost:15672 (guest/guest).
Grafana: http://localhost:3000 → "RealView - Backend Overview" (panels
"Async pipeline runs by outcome" and "retries & dead-lettered jobs").

## Watch a job flow (and a failure → DLQ), no OpenAI cost
```bash
# enqueue via the API producer (returns 202 immediately):
curl -X POST localhost:5001/api/inspections -H 'content-type: application/json' \
     -d '{"property_id":"2203177"}'

# or drive the worker metrics with a canned client (success skips + malformed→DLQ):
DATABASE_URL=postgresql+psycopg2://realview:realview_dev@localhost:5432/realview \
  python messaging/demo_worker.py
```
The real worker container uses the OpenAI-backed client; `demo_worker.py` uses a
canned client so the dashboard shows live data without API cost.

> **Docker Desktop note:** the broker compose pre-seeds an Erlang cookie owned by
> the `rabbitmq` user (a Windows perms quirk). The Linux CI service container
> needs none of this.

## Container status
The **broker** and **worker** now run as real containers (a RabbitMQ container
and a worker container, `Dockerfile.worker`). Still `[LATER]`: a full
build→deploy CD pipeline, and multiple/competing workers, autoscaling, broker
clustering, prefetch tuning.
