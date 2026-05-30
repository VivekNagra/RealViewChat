"""Capture API responses from the current JSON-backed app into baseline/.

Run twice: once before refactoring (label=before) and once after (label=after).
Then compare with scripts/compare_responses.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = PROJECT_ROOT / "web" / "backend" / "app.py"


def load_app():
    spec = importlib.util.spec_from_file_location("backend_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.app


GET_ENDPOINTS = (
    ("properties", "/api/properties"),
    ("stats", "/api/stats"),
    ("summary", "/api/summary"),
    ("feedback", "/api/feedback"),
    ("ground_truth", "/api/ground_truth"),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="e.g. 'before' or 'after'")
    args = parser.parse_args()

    out_dir = PROJECT_ROOT / "baseline" / args.label
    out_dir.mkdir(parents=True, exist_ok=True)

    app = load_app()
    client = app.test_client()

    for name, route in GET_ENDPOINTS:
        resp = client.get(route)
        try:
            payload = resp.get_json()
        except Exception:
            payload = {"_error": "non-json", "_status": resp.status_code,
                       "_body": resp.get_data(as_text=True)}
        dest = out_dir / f"{name}.json"
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
        print(f"  + {dest.relative_to(PROJECT_ROOT)}  (status {resp.status_code})")

    print(f"\nWrote {len(GET_ENDPOINTS)} responses to {out_dir.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
