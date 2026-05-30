"""Rebuild the legacy JSON-file API shapes from the ORM.

Phase-1 goal: every endpoint in web/backend/app.py keeps the exact same
response shape so the React frontend needs no changes. This module is the
sole place that knows the mapping from DB rows back to the legacy dicts.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from realview_chat.db.base import SessionLocal
from realview_chat.db.models import (
    Feedback,
    Image,
    Property,
    Room,
)

# Image scores are conditionally included (only when non-null) to match the
# legacy generator. Room scores are always emitted, even when null.
_IMAGE_SCORE_KEYS = (
    "condition_score",
    "modernity_score",
    "material_score",
    "functionality_score",
)
_ROOM_SCORE_KEYS = (
    "room_condition_score",
    "room_modernity_score",
    "room_material_score",
    "room_functionality_score",
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _serialize_image(img: Image) -> dict:
    out: dict = {
        "filename": img.filename,
        "pass1": {
            "room_type": img.room_type,
            "actionable": bool(img.actionable),
            "confidence": img.pass1_confidence,
        },
        "pass2": [
            {
                "feature_id": feat.feature_id,
                "severity": feat.severity,
                "confidence": feat.confidence,
                "explanation": feat.explanation,
            }
            for feat in sorted(img.features, key=lambda f: f.id)
        ],
    }
    for key in _IMAGE_SCORE_KEYS:
        value = getattr(img, key)
        if value is not None:
            out[key] = value
    return out


def _serialize_room(room: Room) -> dict:
    out: dict = {
        "room_type": room.room_type,
        "confirmed_features": [
            {
                "feature_id": feat.feature_id,
                "severity": feat.severity,
                "confidence": feat.confidence,
                "evidence": feat.evidence,
            }
            for feat in sorted(room.features, key=lambda f: f.id)
        ],
    }
    # rooms always include all four score keys, even when null
    out["room_condition_score"] = room.condition_score
    out["room_modernity_score"] = room.modernity_score
    out["room_material_score"] = room.material_score
    out["room_functionality_score"] = room.functionality_score
    return out


def _serialize_property(prop: Property) -> dict:
    return {
        "property_id": prop.property_id,
        "created_at": _iso(prop.created_at),
        "images": [
            _serialize_image(img)
            for img in sorted(prop.images, key=lambda i: i.id)
        ],
        "rooms": [
            _serialize_room(r) for r in sorted(prop.rooms, key=lambda r: r.id)
        ],
    }


def list_properties(session: Session | None = None) -> list[dict]:
    """Return one dict per property in property_id sort order."""
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        stmt = (
            select(Property)
            .options(
                selectinload(Property.images).selectinload(Image.features),
                selectinload(Property.rooms).selectinload(Room.features),
            )
            .order_by(Property.property_id)
        )
        return [_serialize_property(p) for p in session.scalars(stmt).all()]
    finally:
        if own_session:
            session.close()


def _serialize_feedback(fb: Feedback, prop_ext_id: str, filename: str | None) -> dict:
    """Match the legacy feedback.json entry shape exactly."""
    entry: dict = {"property_id": prop_ext_id, "filename": filename}
    if fb.feedback_type == "verdict":
        entry["feature_id"] = fb.feature_id
        entry["verdict"] = fb.verdict
    elif fb.feedback_type == "classification":
        entry["classification"] = fb.classification
    elif fb.feedback_type == "score":
        entry["score_type"] = fb.score_type
        entry["value"] = fb.score_value
        entry["timestamp"] = _iso(fb.created_at)
    return entry


def list_feedback(session: Session | None = None) -> list[dict]:
    own_session = session is None
    if own_session:
        session = SessionLocal()
    try:
        rows = session.execute(
            select(Feedback, Property.property_id, Image.filename)
            .join(Property, Property.id == Feedback.property_id)
            .outerjoin(Image, Image.id == Feedback.image_id)
            .order_by(Feedback.id)
        ).all()
        return [_serialize_feedback(fb, pid, fname) for fb, pid, fname in rows]
    finally:
        if own_session:
            session.close()
