"""R2 atomic aggregate persistence + R7 data consistency (UNIT, real test DB).

Uses the real migrated Postgres schema so FK cascade and CHECK constraints are
the production ones, not an ORM emulation.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import factories
from realview_chat.db.models import Image, Property

ONE_IMAGE = [{
    "filename": "kitchen1.jpg", "room_type": "kitchen", "actionable": True,
    "pass1_confidence": 0.9, "condition_score": 3, "modernity_score": 2,
    "material_score": 4, "functionality_score": 3,
    "features": [{"feature_id": "water_damage", "severity": "high",
                  "confidence": 0.8, "explanation": "ceiling stain"}],
}]
ONE_ROOM = [{
    "room_type": "kitchen", "condition_score": 3, "modernity_score": 2,
    "material_score": 4, "functionality_score": 3,
    "features": [{"feature_id": "mold", "severity": "medium",
                  "confidence": 0.7, "evidence": "two images agree"}],
}]


@pytest.mark.requirement("R2")
def test_aggregate_persists_and_reloads_atomically(db_session):
    prop = factories.persist(
        db_session, factories.build_property("AGG-1", images=ONE_IMAGE, rooms=ONE_ROOM)
    )
    pid = prop.id
    db_session.expire_all()  # force a reload from the DB

    reloaded = db_session.get(Property, pid)
    assert reloaded.property_id == "AGG-1"
    assert len(reloaded.images) == 1
    assert len(reloaded.images[0].features) == 1
    assert reloaded.images[0].features[0].feature_id == "water_damage"
    assert len(reloaded.rooms) == 1
    assert len(reloaded.rooms[0].features) == 1
    assert len(reloaded.pipeline_runs) == 1


@pytest.mark.requirement("R2")
def test_fk_cascade_deletes_children(db_session):
    prop = factories.persist(
        db_session, factories.build_property("CASCADE-1", images=ONE_IMAGE, rooms=ONE_ROOM)
    )
    pid = prop.id

    def child_count() -> int:
        return db_session.execute(text(
            "SELECT (SELECT count(*) FROM images WHERE property_id = :p) "
            "     + (SELECT count(*) FROM rooms WHERE property_id = :p) "
            "     + (SELECT count(*) FROM image_features WHERE image_id IN "
            "          (SELECT id FROM images WHERE property_id = :p)) "
            "     + (SELECT count(*) FROM room_features WHERE room_id IN "
            "          (SELECT id FROM rooms WHERE property_id = :p))"
        ), {"p": pid}).scalar()

    assert child_count() == 4  # 1 image + 1 room + 1 image_feature + 1 room_feature

    # DB-level ON DELETE CASCADE (raw SQL, bypassing the ORM cascade)
    db_session.execute(text("DELETE FROM properties WHERE id = :p"), {"p": pid})
    assert child_count() == 0


@pytest.mark.requirement("R7")
def test_check_constraint_rejects_out_of_range_score(db_session):
    prop = factories.persist(db_session, factories.build_property("CHK-1"))
    db_session.add(Image(
        property_id=prop.id, filename="bad.jpg", room_type="kitchen",
        condition_score=9,  # CHECK: BETWEEN 1 AND 5
    ))
    with pytest.raises(IntegrityError):
        db_session.flush()
