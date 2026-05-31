"""Shared test infrastructure for RealView's Software Quality suite.

Design choices (see the bachelor report, Software Quality section):

* REAL PostgreSQL test database (`realview_test`), never SQLite. RealView's
  guarantees live in Postgres-specific objects (CHECK constraints, FK cascade,
  the `updated_at` trigger, the `v_property_stats` VIEW, JSONB, a partial index),
  so a SQLite pass would prove nothing.
* The schema is built by the ACTUAL Alembic migrations (`alembic upgrade head`),
  not `metadata.create_all`, so the raw-SQL migration objects (trigger + view)
  exist exactly as in production.
* Per-test isolation via an outer transaction + SAVEPOINT join: the app's own
  `SessionLocal` is rebound to the test connection, so writes made by endpoints
  (e.g. POST /api/feedback) are visible within the test and rolled back after it.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
APP_FILE = REPO_ROOT / "web" / "backend" / "app.py"

# Make the library importable before anything else imports it.
sys.path.insert(0, str(SRC))
# Make shared test helpers (fakes.py, factories.py) importable from subdirs.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Point EVERYTHING at the test DB BEFORE importing the app/db package, because
# the engine is built at import time. An explicit env var beats values from
# .env (python-dotenv's load_dotenv uses override=False).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://realview:realview_dev@localhost:5432/realview_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from realview_chat.db import base as db_base  # noqa: E402
from realview_chat.db.config import get_database_url  # noqa: E402


def _run_migrations() -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    alembic_exe = REPO_ROOT / ".venv" / "Scripts" / "alembic.exe"
    cmd = [str(alembic_exe)] if alembic_exe.exists() else [sys.executable, "-m", "alembic"]
    result = subprocess.run(
        cmd + ["upgrade", "head"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed against the test DB:\n"
            f"{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def _schema():
    # Safety rail: refuse to run the suite against anything but the test DB.
    url = get_database_url()
    assert url.endswith("/realview_test"), f"refusing non-test DB: {url}"
    _run_migrations()
    yield


@pytest.fixture(scope="session")
def flask_app():
    """Load the real Flask app (web/backend/app.py is not a package) once."""
    spec = importlib.util.spec_from_file_location("backend_app", APP_FILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    return module.app


@pytest.fixture()
def db_connection(_schema):
    """Outer transaction + SAVEPOINT join, shared by test code AND the app.

    Rebinding the global SessionLocal to this single connection means every
    `SessionLocal()` / `SessionLocal.begin()` in production code runs inside the
    same transaction; the final rollback undoes everything the test touched.
    """
    connection = db_base.engine.connect()
    transaction = connection.begin()
    db_base.SessionLocal.configure(
        bind=connection, join_transaction_mode="create_savepoint"
    )
    try:
        yield connection
    finally:
        db_base.SessionLocal.configure(
            bind=db_base.engine, join_transaction_mode="conditional_savepoint"
        )
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def db_session(db_connection):
    """A session for repository / serializer unit tests."""
    session = db_base.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_connection, flask_app):
    """Flask test client whose endpoints share the test transaction."""
    return flask_app.test_client()
