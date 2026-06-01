"""Broker connection + topology names + retry policy (env-overridable)."""
from __future__ import annotations

import os

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

# Topology (declared idempotently in topology.declare_topology)
EXCHANGE = "realview.inspections"            # direct exchange for work
DLX = "realview.inspections.dlx"             # dead-letter exchange
WORK_QUEUE = "realview.inspections.work"     # the work queue
DLQ = "realview.inspections.dlq"             # the dead-letter queue
ROUTING_KEY = "inspect"
DLQ_ROUTING_KEY = "dead"

# Bounded retry: a job is retried at most MAX_RETRIES times before it is
# dead-lettered. Retries are immediate (no backoff).
MAX_RETRIES = int(os.getenv("RV_MAX_RETRIES", "3"))
RETRY_HEADER = "x-retry-count"

WORKER_METRICS_PORT = int(os.getenv("RV_WORKER_METRICS_PORT", "9101"))
