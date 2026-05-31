"""Integration tests for the dashboard-critical handlers the React app calls on
load (services/api.js: fetchSummary, fetchStats, fetchFeedback).

These were previously untested. Adding them caught a real regression: the
observability import shadowed `collections.Counter`, which `/api/summary` uses,
turning it into a 500 -- invisible until /metrics showed the status="500" series.
Each test asserts real aggregate behaviour, not just a 200.
"""
from __future__ import annotations

import factories
from realview_chat.db.models import Feedback

KITCHEN_HIGH = {
    "filename": "k.jpg", "room_type": "kitchen", "actionable": True,
    "pass1_confidence": 0.9, "condition_score": 3, "modernity_score": 3,
    "material_score": 3, "functionality_score": 3,
    "features": [{"feature_id": "water_damage", "severity": "high",
                  "confidence": 0.8, "explanation": "x"}],
}


def test_summary_returns_expected_aggregates(db_session, client):
    factories.persist(db_session, factories.build_property("SUM-1", images=[KITCHEN_HIGH]))
    db_session.flush()

    resp = client.get("/api/summary")
    assert resp.status_code == 200  # regression guard: was 500 with the shadowed Counter
    body = resp.get_json()
    assert body["pipeline_funnel"]["total_images"] == 1
    assert body["room_distribution"]["kitchen"] == 1
    assert body["severity_breakdown"]["high"] == 1
    assert "water_damage" in [d["feature_id"] for d in body["damage_frequency"]]


def test_stats_returns_calibration_shape(db_session, client):
    prop = factories.persist(db_session, factories.build_property("ST-1", images=[KITCHEN_HIGH]))
    db_session.add(Feedback(
        property_id=prop.id, image_id=prop.images[0].id,
        feedback_type="classification", classification="correct",
    ))
    db_session.flush()

    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["correct"] == 1
    assert body["total_classified"] == 1
    assert set(body) >= {"correct", "fp", "fn", "precision", "recall", "calibration"}


def test_feedback_get_returns_submitted_entries(db_session, client):
    prop = factories.persist(db_session, factories.build_property("FBG-1", images=[KITCHEN_HIGH]))
    db_session.add(Feedback(
        property_id=prop.id, image_id=prop.images[0].id,
        feedback_type="verdict", feature_id="water_damage", verdict="agree",
    ))
    db_session.flush()

    resp = client.get("/api/feedback")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list)
    assert any(e.get("verdict") == "agree" and e.get("property_id") == "FBG-1" for e in body)
