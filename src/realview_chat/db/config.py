from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# falls back to the local Docker container if DATABASE_URL is not set
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg2://realview:realview_dev@localhost:5432/realview"
)


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)