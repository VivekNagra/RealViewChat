"""Worker: consume inspection jobs, run the pipeline, persist atomically.

Delivery semantics (at-least-once):
- success  -> basic_ack AFTER the DB commit (a crash before ack redelivers,
              never loses the job);
- transient failure -> re-publish with an incremented retry header up to
              MAX_RETRIES, then ack the original (the retry replaces it);
- exhausted / permanent failure -> basic_nack(requeue=False), which dead-letters
              via the queue's configured DLX -> DLQ.

Idempotency: run_pipeline_and_persist skips the pipeline + insert entirely when
the Property already exists, so a redelivered job cannot double-persist (the
external property_id is UNIQUE in the schema as the ultimate guard).
"""
from __future__ import annotations

import functools
import json
import logging
from pathlib import Path

import pika
from sqlalchemy import select

from realview_chat.db.base import SessionLocal
from realview_chat.db.ingest import persist_property_aggregate
from realview_chat.db.models import Property
from realview_chat.pipeline.property_processor import process_property_from_folder

from . import config as c
from . import metrics
from .topology import declare_topology

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASES_ROOT = PROJECT_ROOT / "cases"


class PermanentError(Exception):
    """A failure that must NOT be retried (malformed / unprocessable job)."""


def _images_dir_for(message: dict) -> str:
    if message.get("images_dir"):
        return message["images_dir"]
    return str(CASES_ROOT / f"case_{message['property_id']}")


def run_pipeline_and_persist(message: dict, client) -> bool:
    """Run the Vision pipeline (through the LLMClient seam) and persist the
    Property aggregate in one transaction. Returns True if newly persisted,
    False if it already existed (idempotent skip)."""
    property_id = str(message["property_id"])

    with SessionLocal() as session:
        if session.scalar(select(Property.id).where(Property.property_id == property_id)):
            logger.info("property %s already persisted; skipping (idempotent)", property_id)
            return False

    with metrics.PIPELINE_DURATION.time():
        result = process_property_from_folder(
            images_dir=_images_dir_for(message),
            property_id=property_id,
            client=client,
        )

    with SessionLocal.begin() as session:
        return persist_property_aggregate(session, result)


def _retry_count(properties) -> int:
    headers = getattr(properties, "headers", None) or {}
    try:
        return int(headers.get(c.RETRY_HEADER, 0))
    except (TypeError, ValueError):
        return 0


def _republish_retry(channel, body: bytes, retry_count: int) -> None:
    channel.basic_publish(
        exchange=c.EXCHANGE,
        routing_key=c.ROUTING_KEY,
        body=body,
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            headers={c.RETRY_HEADER: retry_count},
        ),
    )


def handle_message(channel, method, properties, body, *, client) -> None:
    """Process one delivery with manual ack + bounded retry + dead-letter."""
    try:
        message = json.loads(body)
    except (ValueError, TypeError):
        logger.warning("malformed message -> dead-letter")
        metrics.PIPELINE_RUNS.labels(status="failure").inc()
        metrics.DEAD_LETTERED.inc()
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    try:
        run_pipeline_and_persist(message, client)
    except PermanentError:
        logger.exception("permanent failure -> dead-letter")
        metrics.PIPELINE_RUNS.labels(status="failure").inc()
        metrics.DEAD_LETTERED.inc()
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    except Exception:
        metrics.PIPELINE_RUNS.labels(status="failure").inc()
        retries = _retry_count(properties)
        if retries < c.MAX_RETRIES:
            logger.warning("pipeline failed; scheduling retry %d/%d",
                           retries + 1, c.MAX_RETRIES)
            _republish_retry(channel, body, retries + 1)
            metrics.PIPELINE_RETRIES.inc()
            channel.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logger.exception("retries exhausted -> dead-letter")
            metrics.DEAD_LETTERED.inc()
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    metrics.PIPELINE_RUNS.labels(status="success").inc()
    channel.basic_ack(delivery_tag=method.delivery_tag)


def process_next(channel, client) -> bool:
    """Pull and handle a single message (used by tests). False if queue empty."""
    method, properties, body = channel.basic_get(queue=c.WORK_QUEUE, auto_ack=False)
    if method is None:
        return False
    handle_message(channel, method, properties, body, client=client)
    return True


def run_worker(client=None) -> None:  # pragma: no cover (blocking transport loop)
    """Blocking consume loop for the worker process."""
    if client is None:
        from realview_chat.config import load_config
        from realview_chat.openai_client.responses import create_client
        client = create_client(load_config())
    conn = pika.BlockingConnection(pika.URLParameters(c.RABBITMQ_URL))
    channel = conn.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(
        queue=c.WORK_QUEUE,
        on_message_callback=functools.partial(handle_message, client=client),
    )
    logger.info("worker consuming from %s", c.WORK_QUEUE)
    try:
        channel.start_consuming()
    finally:
        conn.close()
