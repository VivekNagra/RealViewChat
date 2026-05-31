"""R5 pipeline correctness (UNIT, Vision mocked via the LLMClient seam).

No network, no API cost, deterministic. Exercises pass1 classification, pass2
per-image features with 1-5 scores, and pass2.5 room-level consolidation
(including the group-by-room-type + chunk-of-4 logic in property_processor).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from fakes import FakeVisionClient
from realview_chat.pipeline import property_processor as pp
from realview_chat.pipeline.pass1 import run_pass1
from realview_chat.pipeline.pass2 import run_pass2
from realview_chat.pipeline.pass25 import run_pass25

pytestmark = pytest.mark.requirement("R5")


def test_pass1_classifies_room_type():
    client = FakeVisionClient(pass1={"url": {"room_type": "kitchen", "actionable": True, "confidence": 0.91}})
    result = run_pass1(client, "url")
    assert result.room_type == "kitchen"
    assert result.actionable is True
    assert result.confidence == pytest.approx(0.91)


def test_pass1_forces_nonactionable_for_nontarget_room():
    # a living_room is outside ALLOWED_ROOMS -> must be coerced non-actionable
    client = FakeVisionClient(pass1={"u": {"room_type": "living_room", "actionable": True, "confidence": 0.8}})
    result = run_pass1(client, "u")
    assert result.room_type == "living_room"
    assert result.actionable is False


def test_pass2_produces_features_with_scores_in_range():
    client = FakeVisionClient(
        pass1={},
        pass2={"u": {
            "features": [
                {"feature_id": "water_damage", "severity": "high",
                 "confidence": 0.88, "explanation": "stain on ceiling"},
            ],
            "condition_score": 2, "modernity_score": 3,
            "material_score": 4, "functionality_score": 5,
        }},
    )
    result = run_pass2(client, "u")
    assert len(result.features) == 1
    feat = result.features[0]
    assert feat.feature_id == "water_damage"
    assert feat.severity == "high"
    for score in (result.condition_score, result.modernity_score,
                  result.material_score, result.functionality_score):
        assert 1 <= score <= 5


def test_pass25_consolidates_room_level_features():
    client = FakeVisionClient(
        pass1={},
        pass25={
            "room_type": "kitchen",
            "confirmed_features": [
                {"feature_id": "mold", "severity": "medium",
                 "confidence": 0.7, "evidence": "two images agree"},
            ],
            "room_condition_score": 3, "room_modernity_score": 2,
            "room_material_score": 3, "room_functionality_score": 4,
        },
    )
    result = run_pass25(client, "kitchen", ["a", "b"])
    assert result.room_type == "kitchen"
    assert len(result.confirmed_features) == 1
    assert result.confirmed_features[0].feature_id == "mold"
    assert result.room_condition_score == 3


def test_process_images_groups_and_chunks_by_room(monkeypatch):
    """5 kitchen + 1 bathroom + 1 bedroom. Expectations:
    - bedroom (non-target) excluded from images and never sent to pass2;
    - pass2 runs on the 6 actionable target images;
    - pass2.5 runs per room_type in chunks of <=4: kitchen (5 imgs) -> 2 calls,
      bathroom (1 img) -> skipped (needs >=2);
    - output dict has the documented shape.
    """
    paths = [Path(f"k{i}.jpg") for i in range(1, 6)] + [Path("b1.jpg"), Path("bed1.jpg")]

    # data-url == filename, so FakeVisionClient can key off it
    monkeypatch.setattr(
        pp, "load_images_as_data_urls",
        lambda image_paths: [(p, p.name) for p in image_paths],
    )

    pass1 = {f"k{i}.jpg": {"room_type": "kitchen", "actionable": True, "confidence": 0.9} for i in range(1, 6)}
    pass1["b1.jpg"] = {"room_type": "bathroom", "actionable": True, "confidence": 0.85}
    pass1["bed1.jpg"] = {"room_type": "bedroom", "actionable": True, "confidence": 0.95}

    pass2 = {
        name: {"features": [{"feature_id": "water_damage", "severity": "low",
                             "confidence": 0.6, "explanation": "x"}],
               "condition_score": 3, "modernity_score": 3,
               "material_score": 3, "functionality_score": 3}
        for name in list(pass1)
    }

    client = FakeVisionClient(
        pass1=pass1, pass2=pass2,
        pass25=lambda room_type, urls: {
            "room_type": room_type, "confirmed_features": [],
            "room_condition_score": 3, "room_modernity_score": 3,
            "room_material_score": 3, "room_functionality_score": 3,
        },
    )

    result = pp._process_images("PROP-1", paths, client)

    assert result["property_id"] == "PROP-1"
    # bedroom excluded -> 6 images (5 kitchen + 1 bathroom)
    filenames = [img["filename"] for img in result["images"]]
    assert "bed1.jpg" not in filenames
    assert len(result["images"]) == 6

    # pass2 ran exactly on the 6 actionable target images
    assert len(client.calls["pass2"]) == 6
    assert "bed1.jpg" not in client.calls["pass2"]

    # pass2.5: kitchen 5 imgs -> ceil(5/4) = 2 chunks; bathroom 1 img -> skipped
    assert len(client.calls["pass25"]) == 2
    assert {c[0] for c in client.calls["pass25"]} == {"kitchen"}
    assert len(result["rooms"]) == 2
    assert all(r["room_type"] == "kitchen" for r in result["rooms"])

    # documented output shape
    sample = result["images"][0]
    assert set(sample.keys()) >= {"filename", "pass1", "pass2"}
    assert {"room_type", "actionable", "confidence"} == set(sample["pass1"].keys())
