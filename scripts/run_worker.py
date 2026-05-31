"""Entrypoint for the async Vision worker.

Starts the Prometheus metrics endpoint, then blocks consuming inspection jobs
from RabbitMQ and persisting Property aggregates. Uses the real OpenAI-backed
LLMClient (the same seam faked in tests).

    RABBITMQ_URL=... DATABASE_URL=... python scripts/run_worker.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from realview_chat.messaging import config as c  # noqa: E402
from realview_chat.messaging.metrics import start_metrics_server  # noqa: E402
from realview_chat.messaging.worker import run_worker  # noqa: E402
from realview_chat.utils.logging import configure_logging  # noqa: E402


def main() -> None:
    configure_logging(level=logging.INFO)
    start_metrics_server(c.WORKER_METRICS_PORT)
    logging.getLogger(__name__).info(
        "worker metrics on :%d, consuming %s", c.WORKER_METRICS_PORT, c.WORK_QUEUE
    )
    run_worker()


if __name__ == "__main__":
    main()
