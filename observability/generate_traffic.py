"""Generate sample backend traffic so the Grafana dashboard shows live data.

Run the backend on the host first (bound so the Prometheus container can reach
it):

    DATABASE_URL=postgresql+psycopg2://realview:realview_dev@localhost:5432/realview \
        python -c "import importlib.util,sys; sys.path.insert(0,'src'); \
        s=importlib.util.spec_from_file_location('a','web/backend/app.py'); \
        m=importlib.util.module_from_spec(s); s.loader.exec_module(m); \
        m.app.run(host='0.0.0.0', port=5001)"

then `docker compose up` in this folder and:

    python observability/generate_traffic.py --seconds 30
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:5001"


def _get(path: str) -> None:
    try:
        urllib.request.urlopen(BASE + path, timeout=5).read()
    except urllib.error.HTTPError:
        pass  # 404s are intentional (they feed the error-rate panel)
    except Exception:
        pass


def _post_feedback(pid: str, filename: str) -> None:
    data = json.dumps({"property_id": pid, "filename": filename,
                       "classification": "fp"}).encode()
    req = urllib.request.Request(
        BASE + "/api/feedback", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()

    # discover a real property + image for valid feedback POSTs
    pid = filename = None
    try:
        props = json.loads(urllib.request.urlopen(BASE + "/api/properties", timeout=5).read())
        if props and props[0].get("images"):
            pid, filename = props[0]["property_id"], props[0]["images"][0]["filename"]
    except Exception:
        pass

    end = time.time() + args.seconds
    cycles = 0
    while time.time() < end:
        _get("/api/properties")
        _get("/api/properties/flagged")
        _get("/api/summary")
        _get("/api/stats")
        _get("/api/feedback")
        _get("/api/properties/UNKNOWN-404")  # error-rate signal
        if pid and cycles % 5 == 0:
            _post_feedback(pid, filename)
        cycles += 1
        time.sleep(0.25)
    print(f"generated {cycles} cycles of traffic over {args.seconds}s")


if __name__ == "__main__":
    main()
