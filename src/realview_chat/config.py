from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    openai_api_key: str
    openai_model: str
    requests_per_minute: int
    max_retries: int
    retry_backoff_seconds: float


def load_config() -> AppConfig:
    load_dotenv(override=True)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing.")

    return AppConfig(
        openai_api_key=api_key,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "60")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        retry_backoff_seconds=float(os.getenv("RETRY_BACKOFF_SECONDS", "1.5")),
    )
