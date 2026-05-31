"""DEMO ONLY -- exercise the worker so the Grafana pipeline panels show live data.

NOT production and NOT a test. Uses a CANNED vision client (no OpenAI, no cost),
publishes a few jobs -- some for an existing property (idempotent success skips)
and some malformed (failure -> dead-letter) -- processes them so the worker
metrics move, then idles serving /metrics for Prometheus to scrape.

    RABBITMQ_URL=... DATABASE_URL=...realview python messaging/demo_worker.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pika  # noqa: E402

from realview_chat.messaging import config as c  # noqa: E402
from realview_chat.messaging import metrics, worker  # noqa: E402
from realview_chat.messaging.producer import build_message, publish  # noqa: E402
from realview_chat.messaging.topology import declare_topology  # noqa: E402


class CannedVision:
    """Returns fixed pipeline output for any image (no network)."""

    def pass1(self, image_data_url):
        return {"room_type": "kitchen", "actionable": True, "confidence": 0.95}

    def pass2(self, image_data_url):
        return {"features": [], "condition_score": 3, "modernity_score": 3,
                "material_score": 3, "functionality_score": 3}

    def pass25(self, room_type, image_data_urls):
        return {"room_type": room_type, "confirmed_features": [],
                "room_condition_score": 3, "room_modernity_score": 3,
                "room_material_score": 3, "room_functionality_score": 3}


def main() -> None:
    existing_id = sys.argv[1] if len(sys.argv) > 1 else "2203177"
    metrics.start_metrics_server(c.WORKER_METRICS_PORT)
    conn = pika.BlockingConnection(pika.URLParameters(c.RABBITMQ_URL))
    ch = conn.channel()
    declare_topology(ch)

    # 3 success jobs (existing property -> idempotent skip) + 2 malformed (-> DLQ)
    for _ in range(3):
        publish(ch, build_message(existing_id))
    for _ in range(2):
        ch.basic_publish(exchange=c.EXCHANGE, routing_key=c.ROUTING_KEY, body=b"not-json")

    client = CannedVision()
    processed = 0
    while worker.process_next(ch, client):
        processed += 1
        if processed > 50:
            break
    print(f"demo processed {processed} messages; metrics on :{c.WORKER_METRICS_PORT}")

    while True:  # idle so Prometheus keeps scraping the live values
        time.sleep(5)


if __name__ == "__main__":
    main()
