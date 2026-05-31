"""R8/R9 worker decision logic (UNIT).

Transport is STUBBED here (a fake channel) so the retry/dead-letter/ack decisions
and the metric increments are tested in isolation, with no broker. The pipeline
is driven through the real FakeVisionClient seam (R8). The same behaviour is
re-verified end-to-end against the REAL broker in tests/integration/test_async.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from fakes import FakeVisionClient
from realview_chat.messaging import config as mc
from realview_chat.messaging import worker


class FakeMethod:
    delivery_tag = 1


class FakeProps:
    def __init__(self, headers=None):
        self.headers = headers or {}


class FakeChannel:
    """Records ack/nack/publish instead of touching a broker."""

    def __init__(self):
        self.acked = []
        self.nacked = []
        self.published = []

    def basic_ack(self, delivery_tag):
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.nacked.append((delivery_tag, requeue))

    def basic_publish(self, exchange, routing_key, body, properties):
        self.published.append((exchange, routing_key, body, properties))


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _msg(pid="W-1"):
    return json.dumps({"property_id": pid}).encode()


def _raise(message, client):
    raise RuntimeError("vision down")


@pytest.mark.requirement("R9")
def test_transient_failure_reschedules_retry_then_acks(monkeypatch):
    monkeypatch.setattr(worker, "run_pipeline_and_persist", _raise)
    ch = FakeChannel()
    before = _sample("realview_pipeline_retries_total")

    worker.handle_message(ch, FakeMethod(), FakeProps(headers={}), _msg(), client=None)

    # one retry republished (x-retry-count=1), original acked, nothing dead-lettered
    assert len(ch.published) == 1
    assert ch.published[0][3].headers[mc.RETRY_HEADER] == 1
    assert ch.acked == [1]
    assert ch.nacked == []
    assert _sample("realview_pipeline_retries_total") == before + 1


@pytest.mark.requirement("R9")
def test_exhausted_retries_dead_letter(monkeypatch):
    monkeypatch.setattr(worker, "run_pipeline_and_persist", _raise)
    ch = FakeChannel()
    before = _sample("realview_dead_lettered_total")

    # already at the bound -> dead-letter (nack requeue=False), no further retry
    props = FakeProps(headers={mc.RETRY_HEADER: mc.MAX_RETRIES})
    worker.handle_message(ch, FakeMethod(), props, _msg(), client=None)

    assert ch.nacked == [(1, False)]
    assert ch.published == []
    assert _sample("realview_dead_lettered_total") == before + 1


@pytest.mark.requirement("R9")
def test_permanent_error_dead_letters_without_retry(monkeypatch):
    def raise_permanent(message, client):
        raise worker.PermanentError("unprocessable job")

    monkeypatch.setattr(worker, "run_pipeline_and_persist", raise_permanent)
    ch = FakeChannel()
    worker.handle_message(ch, FakeMethod(), FakeProps(), _msg(), client=None)

    assert ch.nacked == [(1, False)]
    assert ch.published == []


@pytest.mark.requirement("R9")
def test_malformed_message_dead_letters(monkeypatch):
    ch = FakeChannel()
    worker.handle_message(ch, FakeMethod(), FakeProps(), b"not json", client=None)
    assert ch.nacked == [(1, False)]


@pytest.mark.requirement("R8")
def test_pipeline_failure_increments_failure_metric(db_session, monkeypatch):
    """A raising FakeVisionClient (the real seam) -> realview_pipeline_runs_total
    {status='failure'} increments. Transport stubbed; real test DB for the
    idempotency pre-check."""
    from realview_chat.pipeline import property_processor as pp

    monkeypatch.setattr(pp, "list_image_files", lambda folder: [Path("k1.jpg")])
    monkeypatch.setattr(pp, "load_images_as_data_urls",
                        lambda paths: [(p, p.name) for p in paths])

    class RaisingVision(FakeVisionClient):
        def pass1(self, image_data_url):
            raise RuntimeError("vision API down")

    client = RaisingVision(pass1={})
    ch = FakeChannel()
    before = _sample("realview_pipeline_runs_total", {"status": "failure"})

    worker.handle_message(ch, FakeMethod(), FakeProps(headers={}), _msg("R8-1"), client=client)

    assert _sample("realview_pipeline_runs_total", {"status": "failure"}) == before + 1
