from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = PROJECT_ROOT / "out"
FEEDBACK_PATH = OUT_DIR / "feedback.json"

sys.path.insert(0, str(PROJECT_ROOT / "src"))  # so realview_chat imports resolve

from sqlalchemy import func, select, text  # noqa: E402

from realview_chat.db.base import SessionLocal, engine  # noqa: E402
from realview_chat.db.models import (  # noqa: E402
    Feedback,
    Image,
    ImageFeature,
    PipelineRun,
    Property,
    Room,
    RoomFeature,
)

ALL_TABLES = (
    "properties", "pipeline_runs", "images", "image_features",
    "rooms", "room_features", "feedback",
)


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerate a trailing 'Z'. Return None if absent/invalid."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def load_results_files() -> list[tuple[str, dict]]:
    """Read every out/results_*.json (plus the legacy single-file fallback)."""
    results: list[tuple[str, dict]] = []
    paths = sorted(OUT_DIR.glob("results_*.json"))
    if not paths and (OUT_DIR / "results.json").exists():
        paths = [OUT_DIR / "results.json"]
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "property_id" in data:
                results.append((path.name, data))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  ! skipping {path.name}: {exc}")
    return results


def migrate_one_property(session, data: dict) -> bool:
    """Insert one property and all its children. Returns False if it already exists."""
    external_id = str(data["property_id"])
    created_at = _parse_dt(data.get("created_at"))

    if session.scalar(select(Property).where(Property.property_id == external_id)):
        print(f"  - {external_id}: already migrated, skipping")
        return False

    prop = Property(property_id=external_id)
    if created_at:
        prop.created_at = created_at
        prop.updated_at = created_at
    session.add(prop)
    session.flush()  # assign prop.id

    # one pipeline_run per migrated file; raw JSON preserved in JSONB for audit
    run = PipelineRun(
        property_id=prop.id,
        status="completed",
        model_name=None,  # not recorded in the legacy JSON
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


def migrate_feedback(session) -> int:
    """Migrate feedback.json. Resolves image_id by (property_id, filename) when possible."""
    if not FEEDBACK_PATH.exists():
        print("  - no feedback.json found, skipping")
        return 0
    try:
        with open(FEEDBACK_PATH, encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ! could not read feedback.json: {exc}")
        return 0
    if not isinstance(entries, list):
        return 0

    # lookup maps from external IDs to surrogate keys
    prop_map = {
        pid: pk
        for pid, pk in session.execute(select(Property.property_id, Property.id)).all()
    }
    image_map = {
        (pid, fname): iid
        for pid, fname, iid in session.execute(
            select(Property.property_id, Image.filename, Image.id)
            .join(Image, Image.property_id == Property.id)
        ).all()
    }

    count = 0
    for entry in entries:
        ext_pid = str(entry.get("property_id"))
        filename = entry.get("filename")
        prop_pk = prop_map.get(ext_pid)
        if prop_pk is None:
            print(f"  ! feedback for unknown property {ext_pid!r}, skipping")
            continue

        # discriminate the entry type (frontend sends exactly one kind at a time)
        if entry.get("classification") is not None:
            ftype = "classification"
        elif entry.get("feature_id") is not None and entry.get("verdict") is not None:
            ftype = "verdict"
        elif entry.get("score_type") is not None and entry.get("value") is not None:
            ftype = "score"
        else:
            print(f"  ! unrecognized feedback entry, skipping: {entry}")
            continue

        fb = Feedback(
            property_id=prop_pk,
            image_id=image_map.get((ext_pid, filename)),
            feedback_type=ftype,
            feature_id=entry.get("feature_id"),
            verdict=entry.get("verdict"),
            classification=entry.get("classification"),
            score_type=entry.get("score_type"),
            score_value=entry.get("value"),
        )
        ts = _parse_dt(entry.get("timestamp"))
        if ts:
            fb.created_at = ts
        session.add(fb)
        count += 1
    return count


def reset_tables() -> None:
    """TRUNCATE all tables and restart identity sequences (clean re-run)."""
    stmt = text(
        f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE;"
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    print("Reset: all tables truncated.\n")


def print_counts() -> None:
    print("\nRow counts:")
    with SessionLocal() as session:
        for model in (Property, PipelineRun, Image, ImageFeature, Room, RoomFeature, Feedback):
            n = session.scalar(select(func.count()).select_from(model))
            print(f"  {model.__tablename__:<16} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate JSON files into PostgreSQL.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate all tables before migrating (clean re-run).",
    )
    args = parser.parse_args()

    if args.reset:
        reset_tables()

    print("Migrating pipeline results...")
    migrated = 0
    for name, data in load_results_files():
        try:
            # one transaction per property: all-or-nothing
            with SessionLocal.begin() as session:
                if migrate_one_property(session, data):
                    migrated += 1
                    print(f"  + {data['property_id']}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! failed {name}: {exc}")
    print(f"Migrated {migrated} new properties.")

    print("\nMigrating feedback...")
    try:
        with SessionLocal.begin() as session:
            fb_count = migrate_feedback(session)
        print(f"Migrated {fb_count} feedback entries.")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! feedback migration failed: {exc}")

    print_counts()
    print("\nDone.")


if __name__ == "__main__":
    main()