"""Worker-side Prometheus metrics (the deferred pipeline pass-failure signal).

Exposed by the worker process via prometheus_client.start_http_server. Counter
names end in _total; the duration histogram is in base seconds (Prometheus
conventions, matching the realview_* names used in the web app).
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, start_http_server

PIPELINE_RUNS = Counter(
    "realview_pipeline_runs_total",
    "Vision pipeline runs handled by the worker, by outcome.",
    ["status"],  # success | failure
)
PIPELINE_RETRIES = Counter(
    "realview_pipeline_retries_total",
    "Pipeline jobs re-queued for a bounded retry.",
)
DEAD_LETTERED = Counter(
    "realview_dead_lettered_total",
    "Pipeline jobs routed to the dead-letter queue after exhausting retries.",
)
PIPELINE_DURATION = Histogram(
    "realview_pipeline_duration_seconds",
    "Wall-clock duration of a Vision pipeline run.",
)


def start_metrics_server(port: int) -> None:  # pragma: no cover (transport glue)
    start_http_server(port)
