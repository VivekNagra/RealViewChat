"""Producer: enqueue an inspection job (used by POST /api/inspections)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pika

from . import config as c
from .topology import declare_topology


def build_message(property_id, images_dir=None, images=None) -> dict:
    """The job message schema."""
    return {
        "property_id": str(property_id),
        "images_dir": images_dir,   # optional; worker derives cases/case_<id>
        "images": images,           # optional explicit filename list
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }


def publish(channel, message: dict) -> None:
    """Publish one job onto an existing channel (topology ensured first)."""
    declare_topology(channel)
    channel.basic_publish(
        exchange=c.EXCHANGE,
        routing_key=c.ROUTING_KEY,
        body=json.dumps(message).encode(),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,  # persistent
        ),
    )


def publish_inspection(property_id, images_dir=None, images=None, *, url=None) -> dict:
    """Open a short-lived connection, publish one inspection job, return the
    message. The connection is per-call (simple + robust for low volume)."""
    message = build_message(property_id, images_dir, images)
    conn = pika.BlockingConnection(pika.URLParameters(url or c.RABBITMQ_URL))
    try:
        publish(conn.channel(), message)
    finally:
        conn.close()
    return message
