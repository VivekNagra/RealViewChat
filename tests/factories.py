"""ORM aggregate builders for the repository / serializer / API tests.

Builds a Property aggregate (property -> pipeline_run -> images ->
image_features -> rooms -> room_features) via the relationships, so a single
`session.add(prop)` persists the whole graph through the cascades.
"""
from __future__ import annotations

from datetime import datetime, timezone

from realview_chat.db.models import (
    Image,
    ImageFeature,
    PipelineRun,
    Property,
    Room,
    RoomFeature,
)

FIXED_CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def build_property(
    property_id: str = "P-1",
    *,
    created_at: datetime | None = FIXED_CREATED_AT,
    images: list[dict] | None = None,
    rooms: list[dict] | None = None,
    with_run: bool = True,
) -> Property:
    """Construct (but do not persist) a Property aggregate.

    image spec: {filename, room_type, actionable, pass1_confidence,
                 condition_score?, modernity_score?, material_score?,
                 functionality_score?, features:[{feature_id,severity,
                 confidence,explanation}]}
    room spec:  {room_type, condition_score?, ..., features:[{feature_id,
                 severity,confidence,evidence}]}
    """
    prop = Property(property_id=property_id)
    if created_at is not None:
        prop.created_at = created_at
        prop.updated_at = created_at

    if with_run:
        prop.pipeline_runs.append(PipelineRun(status="completed"))

    for spec in images or []:
        img = Image(
            filename=spec["filename"],
            room_type=spec.get("room_type"),
            actionable=spec.get("actionable", False),
            pass1_confidence=spec.get("pass1_confidence"),
            condition_score=spec.get("condition_score"),
            modernity_score=spec.get("modernity_score"),
            material_score=spec.get("material_score"),
            functionality_score=spec.get("functionality_score"),
        )
        for f in spec.get("features", []):
            img.features.append(ImageFeature(
                feature_id=f["feature_id"],
                severity=f["severity"],
                confidence=f["confidence"],
                explanation=f.get("explanation"),
            ))
        prop.images.append(img)

    for spec in rooms or []:
        room = Room(
            room_type=spec["room_type"],
            condition_score=spec.get("condition_score"),
            modernity_score=spec.get("modernity_score"),
            material_score=spec.get("material_score"),
            functionality_score=spec.get("functionality_score"),
        )
        for f in spec.get("features", []):
            room.features.append(RoomFeature(
                feature_id=f["feature_id"],
                severity=f["severity"],
                confidence=f["confidence"],
                evidence=f.get("evidence"),
            ))
        prop.rooms.append(room)

    return prop


def persist(session, prop: Property) -> Property:
    session.add(prop)
    session.flush()
    return prop
