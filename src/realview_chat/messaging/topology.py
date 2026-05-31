"""Declare the work/dead-letter topology. Idempotent: safe on every startup."""
from __future__ import annotations

from . import config as c


def declare_topology(channel) -> None:
    # work exchange + dead-letter exchange
    channel.exchange_declare(exchange=c.EXCHANGE, exchange_type="direct", durable=True)
    channel.exchange_declare(exchange=c.DLX, exchange_type="direct", durable=True)

    # work queue dead-letters (on nack/reject) to the DLX
    channel.queue_declare(
        queue=c.WORK_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": c.DLX,
            "x-dead-letter-routing-key": c.DLQ_ROUTING_KEY,
        },
    )
    channel.queue_bind(queue=c.WORK_QUEUE, exchange=c.EXCHANGE, routing_key=c.ROUTING_KEY)

    # dead-letter queue
    channel.queue_declare(queue=c.DLQ, durable=True)
    channel.queue_bind(queue=c.DLQ, exchange=c.DLX, routing_key=c.DLQ_ROUTING_KEY)
