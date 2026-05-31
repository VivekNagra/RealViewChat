import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = PROJECT_ROOT / "out"
GROUND_TRUTH_DIR = OUT_DIR / "ground_truth"
CASES_ROOT = PROJECT_ROOT / "cases"

# make src/realview_chat importable when running via `python web/backend/app.py`
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# aliased so they don't shadow collections.Counter used by /api/summary
from prometheus_client import Counter as PromCounter  # noqa: E402
from prometheus_client import Gauge as PromGauge  # noqa: E402
from prometheus_flask_exporter import PrometheusMetrics  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from realview_chat.db.base import SessionLocal, engine  # noqa: E402
from realview_chat.db.models import Feedback, Image, Property, Room  # noqa: E402
from realview_chat.db.serializers import (  # noqa: E402
    _serialize_property,
    list_feedback,
    list_properties,
)

app = Flask(__name__)
CORS(app)

# --- Observability -------------------------------------------------------
# Auto-instrument every request: rate, a latency histogram and status codes,
# grouped by the matched URL rule (low cardinality), exposed at GET /metrics.
# Additive only -- no existing response body changes.
metrics = PrometheusMetrics(app, group_by="url_rule")
metrics.info("realview_app_info", "RealView backend info", version="0.1.0")

# Domain-specific signals reflecting RealView's product reality:
FLAGGED_PROPERTIES = PromGauge(
    "realview_flagged_properties",
    "Properties with >=1 high-severity image feature "
    "(refreshed on each GET /api/properties/flagged).",
)
FEEDBACK_SUBMITTED = PromCounter(
    "realview_feedback_submitted_total",
    "Human feedback submissions accepted, by discriminator type.",
    ["feedback_type"],
)


@app.route("/api/properties", methods=["GET"])
def get_properties():
    return jsonify(list_properties())


@app.route("/api/properties/flagged", methods=["GET"])
def get_flagged_properties():
    """Properties with >=1 high-severity image feature (filter on a non-key
    attribute). Productionised from the Phase-1 benchmark endpoint; the response
    shape [{property_id, high_severity_count}] was validated byte-identical
    across the JSON-file and DB apps."""
    sql = text(
        "SELECT p.property_id, count(*) AS high_severity_count "
        "FROM image_features f "
        "JOIN images i ON i.id = f.image_id "
        "JOIN properties p ON p.id = i.property_id "
        "WHERE f.severity = 'high' "
        "GROUP BY p.property_id "
        "ORDER BY p.property_id"
    )
    with SessionLocal() as session:
        rows = session.execute(sql).all()
    FLAGGED_PROPERTIES.set(len(rows))  # domain signal
    return jsonify([
        {"property_id": pid, "high_severity_count": int(cnt)}
        for pid, cnt in rows
    ])


@app.route("/api/properties/<property_id>", methods=["GET"])
def get_property(property_id):
    """Point lookup by external property_id (served by the unique index on
    properties.property_id). Returns the same per-property dict shape as one
    element of GET /api/properties; 404 when the id is unknown."""
    with SessionLocal() as session:
        stmt = (
            select(Property)
            .options(
                selectinload(Property.images).selectinload(Image.features),
                selectinload(Property.rooms).selectinload(Room.features),
            )
            .where(Property.property_id == property_id)
        )
        prop = session.scalars(stmt).first()
        if prop is None:
            return jsonify({"error": "Property not found"}), 404
        return jsonify(_serialize_property(prop))


@app.route("/api/inspections", methods=["POST"])
def create_inspection():
    """Accept an inspection request and ENQUEUE it for the async worker, then
    return 202 immediately -- the heavy Vision pipeline runs out-of-band. This
    is additive; the existing synchronous endpoints/contract are untouched."""
    body = request.get_json(silent=True) or {}
    property_id = body.get("property_id")
    if not property_id:
        return jsonify({"error": "property_id required"}), 400
    # lazy import so the web app stays decoupled from the broker client
    from realview_chat.messaging.producer import publish_inspection
    try:
        publish_inspection(
            property_id,
            images_dir=body.get("images_dir"),
            images=body.get("images"),
        )
    except Exception as exc:  # noqa: BLE001  (broker unreachable, etc.)
        return jsonify({"error": f"could not enqueue inspection: {exc}"}), 503
    return jsonify({"property_id": str(property_id), "status": "queued"}), 202


@app.route("/api/images/<property_id>/<path:filename>", methods=["GET"])
def serve_image(property_id, filename):
    base = Path(filename).name
    if base != filename:
        return jsonify({"error": "Invalid filename"}), 400
    case_folder = property_id if str(property_id).startswith("case_") else f"case_{property_id}"
    case_dir = CASES_ROOT / case_folder
    if not case_dir.exists() or not case_dir.is_dir():
        return jsonify({"error": "Property image folder not found"}), 404
    path = case_dir / base
    if not path.exists() or not path.is_file():
        return jsonify({"error": "Image not found"}), 404
    return send_from_directory(str(case_dir), base)


@app.route("/api/feedback", methods=["GET"])
def get_feedback():
    return jsonify(list_feedback())


@app.route("/api/feedback", methods=["POST"])
def post_feedback():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "JSON body required"}), 400

    for field in ("property_id", "filename"):
        if field not in body:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    entry = {
        "property_id": body["property_id"],
        "filename": body["filename"],
    }

    has_verdict = "feature_id" in body and "verdict" in body
    has_classification = "classification" in body
    has_score = "score_type" in body and "value" in body

    if not has_verdict and not has_classification and not has_score:
        return jsonify({"error": "Must provide (feature_id + verdict), classification, or (score_type + value)"}), 400

    if has_verdict:
        entry["feature_id"] = body["feature_id"]
        entry["verdict"] = body["verdict"]

    if has_classification:
        valid_classifications = ("correct", "fp", "fn")
        if body["classification"] not in valid_classifications:
            return jsonify({"error": f"classification must be one of: {', '.join(valid_classifications)}"}), 400
        entry["classification"] = body["classification"]

    if has_score:
        valid_score_types = ("condition", "modernity", "material", "functionality")
        score_type = body["score_type"]
        if score_type not in valid_score_types:
            return jsonify({"error": f"score_type must be one of: {', '.join(valid_score_types)}"}), 400
        try:
            value = int(body["value"])
        except (TypeError, ValueError):
            return jsonify({"error": "value must be an integer"}), 400
        if value < 1 or value > 5:
            return jsonify({"error": "value must be between 1 and 5"}), 400
        entry["score_type"] = score_type
        entry["value"] = value
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()

    # discriminator matches the migration script's logic
    if "classification" in entry:
        ftype = "classification"
    elif "feature_id" in entry and "verdict" in entry:
        ftype = "verdict"
    elif "score_type" in entry and "value" in entry:
        ftype = "score"
    else:
        return jsonify({"error": "Internal: could not classify feedback entry"}), 400

    try:
        with SessionLocal.begin() as session:
            ext_pid = str(entry["property_id"])
            prop_pk = session.scalar(
                select(Property.id).where(Property.property_id == ext_pid)
            )
            if prop_pk is None:
                return jsonify({"error": f"Unknown property_id: {ext_pid}"}), 404
            image_pk = session.scalar(
                select(Image.id).where(
                    Image.property_id == prop_pk,
                    Image.filename == entry["filename"],
                )
            )
            session.add(Feedback(
                property_id=prop_pk,
                image_id=image_pk,
                feedback_type=ftype,
                feature_id=entry.get("feature_id"),
                verdict=entry.get("verdict"),
                classification=entry.get("classification"),
                score_type=entry.get("score_type"),
                score_value=entry.get("value"),
            ))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    # approved images get copied to ground truth folder (filesystem behavior preserved)
    if entry.get("classification") == "correct":
        _copy_to_ground_truth(entry["property_id"], entry["filename"])

    FEEDBACK_SUBMITTED.labels(feedback_type=ftype).inc()  # domain signal
    return jsonify({"ok": True, "entry": entry}), 201


def _copy_to_ground_truth(property_id: str, filename: str) -> None:
    base = Path(filename).name
    case_folder = property_id if str(property_id).startswith("case_") else f"case_{property_id}"
    src = CASES_ROOT / case_folder / base

    if not src.exists() or not src.is_file():
        app.logger.warning("Ground truth copy skipped – source not found: %s", src)
        return

    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    dest = GROUND_TRUTH_DIR / f"{property_id}_{base}"
    try:
        shutil.copy2(src, dest)
        app.logger.info("Copied to ground truth: %s -> %s", src, dest)
    except OSError as exc:
        app.logger.error("Failed to copy to ground truth: %s", exc)


def _load_ai_scores_from_properties(properties: list[dict]) -> dict[tuple[str, str], dict]:
    ai_scores: dict[tuple[str, str], dict] = {}
    for data in properties:
        pid = str(data.get("property_id", ""))
        for img in data.get("images", []):
            fname = img.get("filename", "")
            ai_scores[(pid, fname)] = {
                "condition": img.get("condition_score"),
                "modernity": img.get("modernity_score"),
                "material": img.get("material_score"),
                "functionality": img.get("functionality_score"),
            }
    return ai_scores


SCORE_TYPES = ("condition", "modernity", "material", "functionality")


def _compute_calibration(feedback: list[dict], ai_scores: dict) -> dict:
    latest_human: dict[tuple[str, str, str], int] = {}
    for entry in feedback:
        st = entry.get("score_type")
        val = entry.get("value")
        if st and val is not None:
            key = (entry["property_id"], entry["filename"], st)
            latest_human[key] = int(val)

    diffs: dict[str, list[int]] = {st: [] for st in SCORE_TYPES}

    for (pid, fname, score_type), human_val in latest_human.items():
        if score_type not in diffs:
            continue
        ai_vals = ai_scores.get((pid, fname))
        if not ai_vals:
            continue
        ai_val = ai_vals.get(score_type)
        if ai_val is None:
            continue
        diffs[score_type].append(human_val - ai_val)

    result: dict[str, dict] = {}
    for score_type in SCORE_TYPES:
        d = diffs[score_type]
        n = len(d)
        if n == 0:
            result[score_type] = {
                "pairs": 0,
                "mae": None,
                "bias": None,
                "agreement_rate": None,
            }
        else:
            mae = sum(abs(v) for v in d) / n
            bias = sum(d) / n
            agree = sum(1 for v in d if v == 0)
            result[score_type] = {
                "pairs": n,
                "mae": round(mae, 2),
                "bias": round(bias, 2),
                "agreement_rate": round(agree / n * 100, 1),
            }

    all_diffs = [v for st in SCORE_TYPES for v in diffs[st]]
    n_all = len(all_diffs)
    if n_all > 0:
        result["overall"] = {
            "pairs": n_all,
            "mae": round(sum(abs(v) for v in all_diffs) / n_all, 2),
            "bias": round(sum(all_diffs) / n_all, 2),
            "agreement_rate": round(sum(1 for v in all_diffs if v == 0) / n_all * 100, 1),
        }
    else:
        result["overall"] = {"pairs": 0, "mae": None, "bias": None, "agreement_rate": None}

    return result


GRADE_SCALE = [
    (17, "A", "Ny/eksklusiv"),
    (13, "B", "Pæn og moderne"),
    (9, "C", "Brugbar/neutral"),
    (5, "D", "Forældet/slidt"),
    (0, "E", "Renoveringskrævende"),
]


def _total_to_grade(total: int) -> tuple[str, str]:
    for threshold, letter, label in GRADE_SCALE:
        if total >= threshold:
            return letter, label
    return "E", "Renoveringskrævende"


@app.route("/api/stats", methods=["GET"])
def get_stats():
    feedback = list_feedback()

    # dedup: only keep the latest classification per image
    latest: dict[tuple[str, str], str] = {}
    for entry in feedback:
        cls = entry.get("classification")
        if cls:
            latest[(entry["property_id"], entry["filename"])] = cls

    correct = sum(1 for v in latest.values() if v == "correct")
    fp = sum(1 for v in latest.values() if v == "fp")
    fn = sum(1 for v in latest.values() if v == "fn")

    precision = (correct / (correct + fp) * 100) if (correct + fp) > 0 else 0
    recall = (correct / (correct + fn) * 100) if (correct + fn) > 0 else 0

    properties = list_properties()
    ai_scores = _load_ai_scores_from_properties(properties)
    calibration = _compute_calibration(feedback, ai_scores)

    return jsonify({
        "correct": correct,
        "fp": fp,
        "fn": fn,
        "total_classified": correct + fp + fn,
        "precision": round(precision, 1),
        "recall": round(recall, 1),
        "calibration": calibration,
    })


@app.route("/api/reset", methods=["DELETE"])
def reset_benchmarking():
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE feedback RESTART IDENTITY;"))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Failed to clear feedback: {exc}"}), 500

    try:
        if GROUND_TRUTH_DIR.exists():
            shutil.rmtree(GROUND_TRUTH_DIR)
        GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Failed to clear ground truth: {e}"}), 500

    return jsonify({"ok": True})


@app.route("/api/ground_truth", methods=["GET"])
def get_ground_truth():
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}
    files = []
    for p in sorted(GROUND_TRUTH_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in image_extensions:
            files.append(p.name)
    return jsonify(files)


@app.route("/api/ground_truth/<path:filename>", methods=["GET"])
def serve_ground_truth_image(filename):
    base = Path(filename).name
    if base != filename:
        return jsonify({"error": "Invalid filename"}), 400
    path = GROUND_TRUTH_DIR / base
    if not path.exists() or not path.is_file():
        return jsonify({"error": "Image not found"}), 404
    return send_from_directory(str(GROUND_TRUTH_DIR), base)


@app.route("/api/summary", methods=["GET"])
def get_summary():
    total_images = 0
    kitchen_count = 0
    bathroom_count = 0
    kb_actionable = 0
    kb_total = 0
    feature_counter: Counter[str] = Counter()
    proposal_image_counts: list[int] = []

    severity_counter: Counter[str] = Counter()
    kitchen_damage: Counter[str] = Counter()
    bathroom_damage: Counter[str] = Counter()

    p1_confidence_sum = 0.0
    p1_confidence_n = 0
    p2_confidence_sum = 0.0
    p2_confidence_n = 0

    property_damage: dict[str, dict[str, int]] = {}
    property_room_grades: list[dict] = []

    for data in list_properties():
        prop_id = data.get("property_id")
        images = data.get("images", [])
        proposal_image_counts.append(len(images))
        total_images += len(images)
        prop_high = 0
        prop_total_dmg = 0

        for img in images:
            p1 = img.get("pass1", {})
            room = (p1.get("room_type") or "").lower()
            actionable = p1.get("actionable", False)

            p1_conf = p1.get("confidence")
            if p1_conf is not None:
                p1_confidence_sum += p1_conf
                p1_confidence_n += 1

            if room == "kitchen":
                kitchen_count += 1
                kb_total += 1
                if actionable:
                    kb_actionable += 1
            elif room == "bathroom":
                bathroom_count += 1
                kb_total += 1
                if actionable:
                    kb_actionable += 1

            for feature in img.get("pass2", []):
                fid = feature.get("feature_id")
                if not fid:
                    continue

                feature_counter[fid] += 1
                prop_total_dmg += 1

                sev = (feature.get("severity") or "").lower()
                if sev:
                    severity_counter[sev] += 1
                if sev == "high":
                    prop_high += 1

                if room == "kitchen":
                    kitchen_damage[fid] += 1
                elif room == "bathroom":
                    bathroom_damage[fid] += 1

                p2_conf = feature.get("confidence")
                if p2_conf is not None:
                    p2_confidence_sum += p2_conf
                    p2_confidence_n += 1

        property_damage[prop_id] = {"high": prop_high, "total": prop_total_dmg}

        rooms_graded = []
        for room in data.get("rooms", []):
            scores = {
                "condition": room.get("room_condition_score"),
                "modernity": room.get("room_modernity_score"),
                "material": room.get("room_material_score"),
                "functionality": room.get("room_functionality_score"),
            }
            values = [v for v in scores.values() if v is not None]
            if len(values) == 4:
                total = sum(values)
                grade, grade_label = _total_to_grade(total)
                rooms_graded.append({
                    "room_type": room.get("room_type", "unknown"),
                    **scores,
                    "total": total,
                    "grade": grade,
                    "grade_label": grade_label,
                })
        if rooms_graded:
            property_room_grades.append({
                "property_id": prop_id,
                "rooms": rooms_graded,
            })

    actionability_rate = (kb_actionable / kb_total * 100) if kb_total > 0 else 0
    num_proposals = len(proposal_image_counts)
    avg_images = (total_images / num_proposals) if num_proposals > 0 else 0

    at_risk = sorted(
        property_damage.items(),
        key=lambda kv: (-kv[1]["high"], -kv[1]["total"]),
    )[:5]

    return jsonify({
        "pipeline_funnel": {
            "total_images": total_images,
            "kitchen_or_bathroom": kitchen_count + bathroom_count,
        },
        "room_distribution": {
            "kitchen": kitchen_count,
            "bathroom": bathroom_count,
        },
        "damage_frequency": [
            {"feature_id": fid, "count": cnt}
            for fid, cnt in feature_counter.most_common()
        ],
        "room_damage_profiles": {
            "kitchen": [
                {"feature_id": fid, "count": cnt}
                for fid, cnt in kitchen_damage.most_common()
            ],
            "bathroom": [
                {"feature_id": fid, "count": cnt}
                for fid, cnt in bathroom_damage.most_common()
            ],
        },
        "severity_breakdown": {
            "high": severity_counter.get("high", 0),
            "medium": severity_counter.get("medium", 0),
            "low": severity_counter.get("low", 0),
        },
        "confidence_metrics": {
            "pass1_avg": round(p1_confidence_sum / p1_confidence_n, 3) if p1_confidence_n else None,
            "pass1_count": p1_confidence_n,
            "pass2_avg": round(p2_confidence_sum / p2_confidence_n, 3) if p2_confidence_n else None,
            "pass2_count": p2_confidence_n,
        },
        "at_risk_properties": [
            {
                "property_id": pid,
                "high_severity_count": counts["high"],
                "total_damage_count": counts["total"],
            }
            for pid, counts in at_risk
            if counts["high"] > 0
        ],
        "actionability_rate": {
            "actionable_kb_images": kb_actionable,
            "total_kb_images": kb_total,
            "rate_percent": round(actionability_rate, 1),
        },
        "per_proposal_stats": {
            "num_proposals": num_proposals,
            "total_images": total_images,
            "avg_images_per_proposal": round(avg_images, 1),
        },
        "room_grades": property_room_grades,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)  # 5001 bc macOS AirPlay hogs 5000
