"""Structural diff of baseline/before vs baseline/after.

- Sorts list contents by a stable key when keys are ambiguous.
- Compares floats with a small tolerance.
- Reports the first N differences per file, then a summary count.

Usage: python scripts/compare_responses.py [--tolerance 1e-6] [--limit 10]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE = PROJECT_ROOT / "baseline"

# Lists in these JSON paths must be sorted by a stable key before compare.
# Path is a dotted string of keys (lists are crossed transparently when matched
# by their containing key). The value is a function (item) -> sort key.
LIST_SORT_KEYS: dict[str, callable] = {
    # properties endpoint
    "properties": lambda x: x["property_id"],
    "properties.images": lambda x: x["filename"],
    "properties.images.pass2": lambda x: (
        x.get("feature_id", ""),
        x.get("severity", ""),
        x.get("explanation") or "",
    ),
    "properties.rooms": lambda x: (
        x["room_type"],
        sum(int(x.get(k) or 0) for k in (
            "room_condition_score", "room_modernity_score",
            "room_material_score", "room_functionality_score"
        )),
    ),
    "properties.rooms.confirmed_features": lambda x: (
        x.get("feature_id", ""),
        x.get("severity", ""),
        x.get("evidence") or "",
    ),
    # summary endpoint
    "summary.damage_frequency": lambda x: x["feature_id"],
    "summary.room_damage_profiles.kitchen": lambda x: x["feature_id"],
    "summary.room_damage_profiles.bathroom": lambda x: x["feature_id"],
    "summary.at_risk_properties": lambda x: x["property_id"],
    "summary.room_grades": lambda x: x["property_id"],
    "summary.room_grades.rooms": lambda x: (x["room_type"], x["total"]),
    # ground_truth & feedback are flat lists
}


def normalize(value, path: str):
    """Recursively normalize: sort lists where we have a sort key."""
    if isinstance(value, dict):
        return {k: normalize(v, _join(path, k)) for k, v in value.items()}
    if isinstance(value, list):
        items = [normalize(item, path) for item in value]
        key_fn = LIST_SORT_KEYS.get(path)
        if key_fn is not None:
            try:
                items = sorted(items, key=key_fn)
            except (KeyError, TypeError) as exc:
                print(f"  ! could not sort {path}: {exc}")
        return items
    return value


def _join(parent: str, key: str) -> str:
    return key if not parent else f"{parent}.{key}"


def diff(a, b, tol: float, path: str, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        # treat int/float as comparable
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            out.append(f"{path}: type mismatch {type(a).__name__} vs {type(b).__name__}")
            return
    if isinstance(a, dict):
        keys = set(a) | set(b)
        for k in sorted(keys):
            if k not in a:
                out.append(f"{_join(path, k)}: only in AFTER ({b[k]!r})")
            elif k not in b:
                out.append(f"{_join(path, k)}: only in BEFORE ({a[k]!r})")
            else:
                diff(a[k], b[k], tol, _join(path, k), out, limit)
            if len(out) >= limit:
                return
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length differs {len(a)} vs {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, tol, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                return
    elif isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            if a != b:
                out.append(f"{path}: {a!r} vs {b!r}")
        elif abs(a - b) > tol:
            out.append(f"{path}: {a} vs {b} (diff {a - b:+g})")
    else:
        if a != b:
            out.append(f"{path}: {a!r} vs {b!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--limit", type=int, default=20,
                        help="max diffs to report per file")
    args = parser.parse_args()

    before_dir = BASELINE / "before"
    after_dir = BASELINE / "after"
    if not before_dir.exists() or not after_dir.exists():
        print(f"Missing {before_dir} or {after_dir}. Run capture_baseline first.")
        return 2

    files = sorted({p.name for p in before_dir.glob("*.json")}
                   | {p.name for p in after_dir.glob("*.json")})

    total_diffs = 0
    for name in files:
        bp = before_dir / name
        ap = after_dir / name
        print(f"\n--- {name} ---")
        if not bp.exists():
            print(f"  ! missing in BEFORE")
            total_diffs += 1
            continue
        if not ap.exists():
            print(f"  ! missing in AFTER")
            total_diffs += 1
            continue
        with open(bp, encoding="utf-8") as f:
            a = json.load(f)
        with open(ap, encoding="utf-8") as f:
            b = json.load(f)
        root = name.rsplit(".", 1)[0]  # strip .json
        a = normalize(a, root)
        b = normalize(b, root)
        out: list[str] = []
        diff(a, b, args.tolerance, root, out, args.limit)
        if not out:
            print("  OK")
        else:
            for line in out:
                print(f"  - {line}")
            total_diffs += len(out)

    print(f"\nTotal differences reported: {total_diffs}")
    return 0 if total_diffs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
