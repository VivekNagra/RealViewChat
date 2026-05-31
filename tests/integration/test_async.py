"""R9/R11 async integration against a REAL RabbitMQ broker + the real test DB.

These use the `rabbitmq` fixture (skips if no broker locally; CI always has one)
and the existing transactional DB isolation. The worker runs the pipeline
through the real FakeVisionClient seam (image LOADING is monkeypatched -- the
filesystem, not the Vision client) and persists via the shared atomic unit.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import func, select

from fakes import FakeVisionClient
from realview_chat.db.base import SessionLocal
from realview_chat.db.models import Property
from realview_chat.messaging import config as mc
from realview_chat.messaging import worker
from realview_chat.messaging.producer import build_message, publish
from realview_chat.pipeline import property_processor as pp


def _fake_vision(monkeypatch):
    """Two actionable kitchen images via the real seam; filesystem stubbed."""
    monkeypatch.setattr(pp, "list_image_files",
                        lambda folder: [Path("k1.jpg"), Path("k2.jpg")])
    monkeypatch.setattr(pp, "load_images_as_data_urls",
                        lambda paths: [(p, p.name) for p in paths])
    p1 = {n: {"room_type": "kitchen", "actionable": True, "confidence": 0.9}
          for n in ("k1.jpg", "k2.jpg")}
    p2 = {n: {"features": [], "condition_score": 3, "modernity_score": 3,
              "material_score": 3, "functionality_score": 3} for n in p1}
    return FakeVisionClient(
        pass1=p1, pass2=p2,
        pass25=lambda room_type, urls: {
            "room_type": room_type, "confirmed_features": [],
            "room_condition_score": 3, "room_modernity_score": 3,
            "room_material_score": 3, "room_functionality_score": 3,
        },
    )


def _depth(channel, queue):
    return channel.queue_declare(queue=queue, durable=True, passive=True).method.message_count


def _count(property_id):
    with SessionLocal() as s:
        return s.scalar(
            select(func.count()).select_from(Property).where(Property.property_id == property_id)
        )


@pytest.mark.requirement("R11")
def test_producer_enqueues_without_processing(rabbitmq):
    publish(rabbitmq, build_message("PUB-1"))
    # the job is parked in the work queue; nothing consumed it
    assert _depth(rabbitmq, mc.WORK_QUEUE) == 1
    assert _count("PUB-1") == 0


@pytest.mark.requirement("R11")
def test_post_inspections_returns_202_and_publishes(client, rabbitmq):
    """POST /api/inspections accepts + enqueues without running the pipeline."""
    import json as _json

    resp = client.post("/api/inspections", json={"property_id": "HTTP-1"})
    assert resp.status_code == 202
    assert resp.get_json() == {"property_id": "HTTP-1", "status": "queued"}

    method, _props, body = rabbitmq.basic_get(mc.WORK_QUEUE, auto_ack=True)
    assert method is not None
    assert _json.loads(body)["property_id"] == "HTTP-1"


@pytest.mark.requirement("R11")
def test_end_to_end_enqueue_consume_persist(rabbitmq, db_connection, monkeypatch):
    client = _fake_vision(monkeypatch)
    publish(rabbitmq, build_message("ASYNC-1", images_dir="x"))

    assert worker.process_next(rabbitmq, client) is True  # consume -> pipeline -> persist
    assert _count("ASYNC-1") == 1
    assert _depth(rabbitmq, mc.WORK_QUEUE) == 0  # acked, queue drained


@pytest.mark.requirement("R9")
def test_success_path_acks_exactly_once(rabbitmq, db_connection, monkeypatch):
    client = _fake_vision(monkeypatch)
    publish(rabbitmq, build_message("ACK-1", images_dir="x"))
    assert worker.process_next(rabbitmq, client) is True
    # nothing redelivered / requeued: a second poll finds the queue empty
    assert worker.process_next(rabbitmq, client) is False
    assert _count("ACK-1") == 1


@pytest.mark.requirement("R9")
def test_failing_job_dead_letters_after_max_retries(rabbitmq, monkeypatch):
    def boom(message, client):
        raise RuntimeError("vision down")

    monkeypatch.setattr(worker, "run_pipeline_and_persist", boom)
    before_dl = REGISTRY.get_sample_value("realview_dead_lettered_total") or 0.0

    publish(rabbitmq, build_message("DLQ-1"))
    handled = 0
    while worker.process_next(rabbitmq, client=None):
        handled += 1
        if handled > 10:  # safety
            break

    assert handled == mc.MAX_RETRIES + 1          # original + N retries
    assert _depth(rabbitmq, mc.WORK_QUEUE) == 0    # nothing left to retry
    assert _depth(rabbitmq, mc.DLQ) == 1           # parked, not lost
    assert (REGISTRY.get_sample_value("realview_dead_lettered_total") or 0.0) == before_dl + 1


@pytest.mark.requirement("R9")
def test_duplicate_delivery_persists_once(rabbitmq, db_connection, monkeypatch):
    client = _fake_vision(monkeypatch)
    msg = build_message("DUP-1", images_dir="x")

    publish(rabbitmq, msg)
    assert worker.process_next(rabbitmq, client) is True
    publish(rabbitmq, msg)  # at-least-once redelivery of the SAME job
    assert worker.process_next(rabbitmq, client) is True

    assert _count("DUP-1") == 1  # idempotent: UNIQUE property_id, no double-persist
