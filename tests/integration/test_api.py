"""API integration tests (Flask test client + real test DB).

R1 list contract+status, R4 point lookup + 404, R3 flagged returns EXACTLY the
flagged set (correctness, not just latency).
"""
from __future__ import annotations

import pytest

import factories

KITCHEN_IMG = {"filename": "k.jpg", "room_type": "kitchen", "actionable": True,
               "pass1_confidence": 0.9}


def _img_with_features(filename, severities):
    return {
        "filename": filename, "room_type": "kitchen", "actionable": True,
        "pass1_confidence": 0.9,
        "features": [
            {"feature_id": "water_damage", "severity": sev, "confidence": 0.8,
             "explanation": "x"}
            for sev in severities
        ],
    }


@pytest.mark.requirement("R1")
def test_list_properties_shape_and_status(db_session, client):
    factories.persist(db_session, factories.build_property("LP-1", images=[KITCHEN_IMG]))
    factories.persist(db_session, factories.build_property("LP-2", images=[KITCHEN_IMG]))
    db_session.flush()

    resp = client.get("/api/properties")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list) and len(body) == 2
    # sorted by property_id; documented per-property keys
    assert [p["property_id"] for p in body] == ["LP-1", "LP-2"]
    assert set(body[0].keys()) == {"property_id", "created_at", "images", "rooms"}


@pytest.mark.requirement("R4")
def test_point_lookup_returns_correct_property(db_session, client):
    factories.persist(db_session, factories.build_property("PT-1", images=[KITCHEN_IMG]))
    db_session.flush()

    resp = client.get("/api/properties/PT-1")
    assert resp.status_code == 200
    assert resp.get_json()["property_id"] == "PT-1"


@pytest.mark.requirement("R4")
def test_point_lookup_missing_id_returns_404(client):
    resp = client.get("/api/properties/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.requirement("R3")
def test_flagged_returns_exactly_the_flagged_set(db_session, client):
    # P-A: 2 high features -> flagged with count 2
    factories.persist(db_session, factories.build_property(
        "P-A", images=[_img_with_features("a.jpg", ["high", "high"])]))
    # P-B: only medium/low -> NOT flagged
    factories.persist(db_session, factories.build_property(
        "P-B", images=[_img_with_features("b.jpg", ["medium", "low"])]))
    # P-C: 1 high feature -> flagged with count 1
    factories.persist(db_session, factories.build_property(
        "P-C", images=[_img_with_features("c.jpg", ["high"])]))
    db_session.flush()

    resp = client.get("/api/properties/flagged")
    assert resp.status_code == 200
    assert resp.get_json() == [
        {"property_id": "P-A", "high_severity_count": 2},
        {"property_id": "P-C", "high_severity_count": 1},
    ]
