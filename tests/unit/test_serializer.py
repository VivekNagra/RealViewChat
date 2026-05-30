"""R1 contract preservation (UNIT + endpoint agreement).

The serializer is THE REST contract. These tests pin the exact dict shape so the
upcoming query-optimisation phase cannot silently change the wire format. They
also assert the live endpoint reproduces the serializer output byte-for-byte.

Contract subtleties pinned here:
- image score keys are OMITTED when null (image B has none);
- room score keys are ALWAYS present, even when null (room 2 has nulls);
- images and rooms preserve insertion order (sorted by surrogate id).
"""
from __future__ import annotations

import json

import pytest

import factories
from realview_chat.db.serializers import _serialize_property

IMAGES = [
    {"filename": "a.jpg", "room_type": "kitchen", "actionable": True,
     "pass1_confidence": 0.9, "condition_score": 3, "modernity_score": 2,
     "material_score": 4, "functionality_score": 5,
     "features": [{"feature_id": "water_damage", "severity": "high",
                   "confidence": 0.8, "explanation": "stain"}]},
    # image B: no scores at all -> score keys must be omitted
    {"filename": "b.jpg", "room_type": "bathroom", "actionable": True,
     "pass1_confidence": 0.7, "features": []},
]
ROOMS = [
    {"room_type": "kitchen", "condition_score": 3, "modernity_score": 2,
     "material_score": 4, "functionality_score": 3,
     "features": [{"feature_id": "mold", "severity": "medium",
                   "confidence": 0.7, "evidence": "two images agree"}]},
    # room 2: null scores -> keys still present with null values
    {"room_type": "bathroom", "features": []},
]

EXPECTED = {
    "property_id": "CONTRACT-1",
    "created_at": "2026-01-02T03:04:05+00:00",
    "images": [
        {"filename": "a.jpg",
         "pass1": {"room_type": "kitchen", "actionable": True, "confidence": 0.9},
         "pass2": [{"feature_id": "water_damage", "severity": "high",
                    "confidence": 0.8, "explanation": "stain"}],
         "condition_score": 3, "modernity_score": 2,
         "material_score": 4, "functionality_score": 5},
        {"filename": "b.jpg",
         "pass1": {"room_type": "bathroom", "actionable": True, "confidence": 0.7},
         "pass2": []},
    ],
    "rooms": [
        {"room_type": "kitchen",
         "confirmed_features": [{"feature_id": "mold", "severity": "medium",
                                 "confidence": 0.7, "evidence": "two images agree"}],
         "room_condition_score": 3, "room_modernity_score": 2,
         "room_material_score": 4, "room_functionality_score": 3},
        {"room_type": "bathroom", "confirmed_features": [],
         "room_condition_score": None, "room_modernity_score": None,
         "room_material_score": None, "room_functionality_score": None},
    ],
}


def _seed(session):
    return factories.persist(
        session, factories.build_property("CONTRACT-1", images=IMAGES, rooms=ROOMS)
    )


@pytest.mark.requirement("R1")
def test_serializer_reproduces_contract(db_session):
    prop = _seed(db_session)
    assert _serialize_property(prop) == EXPECTED


@pytest.mark.requirement("R1")
def test_endpoint_matches_serializer_byte_for_byte(db_session, client):
    prop = _seed(db_session)
    db_session.flush()
    serialized = _serialize_property(prop)

    resp = client.get("/api/properties/CONTRACT-1")
    assert resp.status_code == 200
    assert resp.get_json() == serialized == EXPECTED
    # byte-for-byte once both are canonicalised
    assert json.dumps(resp.get_json(), sort_keys=True) == json.dumps(EXPECTED, sort_keys=True)


@pytest.mark.requirement("R1")
def test_feedback_serializer_reproduces_each_entry_type(db_session):
    """list_feedback() rebuilds the legacy feedback.json entry shapes: one per
    discriminator (classification / verdict / score), keyed back to the external
    property_id + image filename."""
    from realview_chat.db.models import Feedback
    from realview_chat.db.serializers import list_feedback

    prop = factories.persist(db_session, factories.build_property(
        "FB-1", images=[{"filename": "img.jpg", "room_type": "kitchen",
                         "actionable": True, "pass1_confidence": 0.9}]))
    image_id = prop.images[0].id
    db_session.add_all([
        Feedback(property_id=prop.id, image_id=image_id,
                 feedback_type="classification", classification="correct"),
        Feedback(property_id=prop.id, image_id=image_id,
                 feedback_type="verdict", feature_id="water_damage", verdict="agree"),
        Feedback(property_id=prop.id, image_id=image_id,
                 feedback_type="score", score_type="condition", score_value=4),
    ])
    db_session.flush()

    entries = list_feedback(db_session)
    assert len(entries) == 3
    by_type = {tuple(sorted(e.keys())): e for e in entries}

    classification = next(e for e in entries if "classification" in e)
    assert classification == {"property_id": "FB-1", "filename": "img.jpg",
                              "classification": "correct"}

    verdict = next(e for e in entries if "verdict" in e)
    assert verdict == {"property_id": "FB-1", "filename": "img.jpg",
                       "feature_id": "water_damage", "verdict": "agree"}

    score = next(e for e in entries if "score_type" in e)
    assert score["property_id"] == "FB-1"
    assert score["filename"] == "img.jpg"
    assert score["score_type"] == "condition"
    assert score["value"] == 4
    assert "timestamp" in score  # score entries carry a timestamp
    assert by_type  # (silences lint: all three discriminators present)


@pytest.mark.requirement("R1")
def test_list_helpers_open_their_own_session_when_none_passed(db_session):
    """list_properties()/list_feedback() with no session argument must open and
    close their own session (the path used by the Flask endpoints)."""
    from realview_chat.db.serializers import list_feedback, list_properties

    _seed(db_session)
    db_session.flush()

    props = list_properties()  # own-session path
    assert any(p["property_id"] == "CONTRACT-1" for p in props)
    assert isinstance(list_feedback(), list)  # own-session path
