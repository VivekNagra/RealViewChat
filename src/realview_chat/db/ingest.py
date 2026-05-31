"""Atomic persistence of one Property aggregate.

This is the single source of truth for turning a canonical pipeline-result dict
(property -> images/features -> rooms/features) into rows, in ONE transaction.
Both the offline migration script and the async worker call it, so the
transaction boundary and idempotency are defined in exactly one place.

Idempotency: keyed on the external ``property_id`` (UNIQUE in the schema). If a
property with that id already exists the function inserts nothing and returns
False -- so an at-least-once redelivery of the same job cannot double-persist.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from realview_chat.db.models import (
    Image,
    ImageFeature,
    PipelineRun,
    Property,
    Room,
    RoomFeature,
)

logger = logging.getLogger(__name__)


def parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerate a trailing 'Z'. None if absent/invalid."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def property_exists(session: Session, external_id: str) -> bool:
    return session.scalar(
        select(Property.id).where(Property.property_id == str(external_id))
    ) is not None


def persist_property_aggregate(session: Session, data: dict) -> bool:
    """Insert one Property and all its children in the caller's transaction.

    Returns True if newly inserted, False if a property with the same external
    id already exists (idempotent no-op). The caller owns the transaction
    boundary (e.g. ``with SessionLocal.begin() as session: ...``).
    """
    external_id = str(data["property_id"])
    created_at = parse_dt(data.get("created_at"))

    if property_exists(session, external_id):
        logger.info("property %s already persisted; skipping (idempotent)", external_id)
        return False

    prop = Property(property_id=external_id)
    if created_at:
        prop.created_at = created_at
        prop.updated_at = created_at
    session.add(prop)
    session.flush()  # assign prop.id

    # one pipeline_run per result; raw JSON preserved in JSONB for audit
    run = PipelineRun(
        property_id=prop.id,
        status="completed",
        model_name=None,
        raw_output=data,
    )
    if created_at:
        run.started_at = created_at
        run.finished_at = created_at
    session.add(run)
    session.flush()  # assign run.id

    for img in data.get("images", []):
        pass1 = img.get("pass1", {})
        image = Image(
            property_id=prop.id,
            pipeline_run_id=run.id,
            filename=img["filename"],
            room_type=pass1.get("room_type"),
            actionable=bool(pass1.get("actionable", False)),
            pass1_confidence=pass1.get("confidence"),
            condition_score=img.get("condition_score"),
            modernity_score=img.get("modernity_score"),
            material_score=img.get("material_score"),
            functionality_score=img.get("functionality_score"),
        )
        session.add(image)
        session.flush()  # assign image.id

        for feat in img.get("pass2", []):
            session.add(ImageFeature(
                image_id=image.id,
                feature_id=feat["feature_id"],
                severity=feat["severity"],
                confidence=float(feat["confidence"]),
                explanation=feat.get("explanation"),
            ))

    for room in data.get("rooms", []):
        room_obj = Room(
            property_id=prop.id,
            pipeline_run_id=run.id,
            room_type=room["room_type"],
            condition_score=room.get("room_condition_score"),
            modernity_score=room.get("room_modernity_score"),
            material_score=room.get("room_material_score"),
            functionality_score=room.get("room_functionality_score"),
        )
        session.add(room_obj)
        session.flush()  # assign room_obj.id

        for feat in room.get("confirmed_features", []):
            session.add(RoomFeature(
                room_id=room_obj.id,
                feature_id=feat["feature_id"],
                severity=feat["severity"],
                confidence=float(feat["confidence"]),
                evidence=feat.get("evidence"),
            ))

    return True
