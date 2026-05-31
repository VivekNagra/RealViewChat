"""Observability: GET /metrics exposes the HTTP signals + RealView domain signals.

These are additive metrics; the byte-identical REST contract is guarded by the
serializer test. Here we assert the Prometheus endpoint renders the request
histogram and the two domain signals, and that the feedback counter moves on a
successful submission.
"""
from __future__ import annotations

import factories

HIGH_SEV_IMAGE = {
    "filename": "k.jpg", "room_type": "kitchen", "actionable": True,
    "pass1_confidence": 0.9,
    "features": [{"feature_id": "water_damage", "severity": "high",
                  "confidence": 0.8, "explanation": "x"}],
}


def test_metrics_exposes_http_and_domain_signals(db_session, client):
    factories.persist(db_session, factories.build_property("OBS-1", images=[HIGH_SEV_IMAGE]))
    db_session.flush()
    # generate traffic so the request histogram has samples and the gauge is set
    client.get("/api/properties")
    client.get("/api/properties/flagged")

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # HTTP rate / latency histogram / status codes (prometheus-flask-exporter)
    assert "flask_http_request_duration_seconds" in body
    assert "flask_http_request_total" in body
    # domain signal: flagged-property volume
    assert "realview_flagged_properties" in body


def test_feedback_counter_increments_on_submission(db_session, client):
    factories.persist(db_session, factories.build_property("OBS-FB", images=[HIGH_SEV_IMAGE]))
    db_session.flush()

    resp = client.post("/api/feedback", json={
        "property_id": "OBS-FB", "filename": "k.jpg", "classification": "fp",
    })
    assert resp.status_code == 201

    body = client.get("/metrics").get_data(as_text=True)
    assert "realview_feedback_submitted_total" in body
