from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from realview_chat.db.base import Base

# Enum value lists — kept in sync with openai_client/schemas.py
ROOM_TYPES = (
    "bedroom", "bathroom", "kitchen", "living_room", "dining_room",
    "hallway", "garage", "exterior", "unknown",
)
FEATURE_IDS = (
    "water_damage", "mold", "broken_fixture", "stained_carpet", "cracked_tile",
)
SEVERITIES = ("low", "medium", "high")
SCORE_TYPES = ("condition", "modernity", "material", "functionality")
CLASSIFICATIONS = ("correct", "fp", "fn")
VERDICTS = ("agree", "disagree")
FEEDBACK_TYPES = ("classification", "verdict", "score")
RUN_STATUSES = ("running", "completed", "failed")


def _in(col: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({quoted})"


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    pipeline_runs: Mapped[list["PipelineRun"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    images: Mapped[list["Image"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    rooms: Mapped[list["Room"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["Feedback"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="running"
    )
    model_name: Mapped[str | None] = mapped_column(String(100))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    raw_output: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    property: Mapped["Property"] = relationship(back_populates="pipeline_runs")
    images: Mapped[list["Image"]] = relationship(back_populates="pipeline_run")
    rooms: Mapped[list["Room"]] = relationship(back_populates="pipeline_run")

    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_pipeline_runs_status"),
        Index("ix_pipeline_runs_property_id", "property_id"),
        # partial index (PostgreSQL-specific optimization)
        Index(
            "ix_pipeline_runs_running",
            "id",
            postgresql_where=text("status = 'running'"),
        ),
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    room_type: Mapped[str | None] = mapped_column(String(20))
    actionable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pass1_confidence: Mapped[float | None] = mapped_column(Float)
    condition_score: Mapped[int | None] = mapped_column(SmallInteger)
    modernity_score: Mapped[int | None] = mapped_column(SmallInteger)
    material_score: Mapped[int | None] = mapped_column(SmallInteger)
    functionality_score: Mapped[int | None] = mapped_column(SmallInteger)

    property: Mapped["Property"] = relationship(back_populates="images")
    pipeline_run: Mapped["PipelineRun | None"] = relationship(back_populates="images")
    features: Mapped[list["ImageFeature"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="image")

    __table_args__ = (
        CheckConstraint(_in("room_type", ROOM_TYPES), name="ck_images_room_type"),
        CheckConstraint("condition_score BETWEEN 1 AND 5", name="ck_images_condition"),
        CheckConstraint("modernity_score BETWEEN 1 AND 5", name="ck_images_modernity"),
        CheckConstraint("material_score BETWEEN 1 AND 5", name="ck_images_material"),
        CheckConstraint(
            "functionality_score BETWEEN 1 AND 5", name="ck_images_functionality"
        ),
        CheckConstraint(
            "pass1_confidence >= 0 AND pass1_confidence <= 1",
            name="ck_images_pass1_confidence",
        ),
        UniqueConstraint("property_id", "filename", name="uq_images_property_filename"),
        Index("ix_images_property_id", "property_id"),
    )


class ImageFeature(Base):
    __tablename__ = "image_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey("images.id", ondelete="CASCADE"), nullable=False
    )
    feature_id: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)

    image: Mapped["Image"] = relationship(back_populates="features")

    __table_args__ = (
        CheckConstraint(_in("feature_id", FEATURE_IDS), name="ck_imgfeat_feature_id"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_imgfeat_severity"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_imgfeat_confidence"
        ),
        Index("ix_image_features_image_id", "image_id"),
    )


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    room_type: Mapped[str] = mapped_column(String(20), nullable=False)
    condition_score: Mapped[int | None] = mapped_column(SmallInteger)
    modernity_score: Mapped[int | None] = mapped_column(SmallInteger)
    material_score: Mapped[int | None] = mapped_column(SmallInteger)
    functionality_score: Mapped[int | None] = mapped_column(SmallInteger)

    property: Mapped["Property"] = relationship(back_populates="rooms")
    pipeline_run: Mapped["PipelineRun | None"] = relationship(back_populates="rooms")
    features: Mapped[list["RoomFeature"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # pass2.5 chunks images in groups of 4, so a property can legitimately
        # produce multiple consolidations per room_type — no uniqueness here.
        CheckConstraint(_in("room_type", ROOM_TYPES), name="ck_rooms_room_type"),
        CheckConstraint("condition_score BETWEEN 1 AND 5", name="ck_rooms_condition"),
        CheckConstraint("modernity_score BETWEEN 1 AND 5", name="ck_rooms_modernity"),
        CheckConstraint("material_score BETWEEN 1 AND 5", name="ck_rooms_material"),
        CheckConstraint(
            "functionality_score BETWEEN 1 AND 5", name="ck_rooms_functionality"
        ),
        Index("ix_rooms_property_room_type", "property_id", "room_type"),
    )


class RoomFeature(Base):
    __tablename__ = "room_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    feature_id: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)

    room: Mapped["Room"] = relationship(back_populates="features")

    __table_args__ = (
        CheckConstraint(_in("feature_id", FEATURE_IDS), name="ck_roomfeat_feature_id"),
        CheckConstraint(_in("severity", SEVERITIES), name="ck_roomfeat_severity"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_roomfeat_confidence"
        ),
        Index("ix_room_features_room_id", "room_id"),
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), nullable=False
    )
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("images.id", ondelete="SET NULL")
    )
    feedback_type: Mapped[str] = mapped_column(String(20), nullable=False)
    feature_id: Mapped[str | None] = mapped_column(String(50))
    verdict: Mapped[str | None] = mapped_column(String(10))
    classification: Mapped[str | None] = mapped_column(String(10))
    score_type: Mapped[str | None] = mapped_column(String(20))
    score_value: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    property: Mapped["Property"] = relationship(back_populates="feedback")
    image: Mapped["Image | None"] = relationship(back_populates="feedback")

    __table_args__ = (
        CheckConstraint(_in("feedback_type", FEEDBACK_TYPES), name="ck_feedback_type"),
        CheckConstraint(
            "verdict IS NULL OR " + _in("verdict", VERDICTS), name="ck_feedback_verdict"
        ),
        CheckConstraint(
            "classification IS NULL OR " + _in("classification", CLASSIFICATIONS),
            name="ck_feedback_classification",
        ),
        CheckConstraint(
            "score_type IS NULL OR " + _in("score_type", SCORE_TYPES),
            name="ck_feedback_score_type",
        ),
        CheckConstraint(
            "score_value IS NULL OR score_value BETWEEN 1 AND 5",
            name="ck_feedback_score_value",
        ),
        Index("ix_feedback_property_id", "property_id"),
        Index(
            "ix_feedback_latest_class", "image_id", "feedback_type", "created_at"
        ),
    )