"""STEP 1 acceptance: the test DB is migration-built and per-test isolated."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from realview_chat.db.models import Property


@pytest.mark.requirement("R7")
def test_schema_built_from_migrations(db_connection):
    """The VIEW and trigger exist ONLY if the real migrations ran (create_all
    would have skipped these raw-SQL objects)."""
    view = db_connection.execute(
        text("SELECT to_regclass('public.v_property_stats')")
    ).scalar()
    assert view is not None, "v_property_stats VIEW missing -> migrations did not run"

    trig = db_connection.execute(
        text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_properties_updated_at'")
    ).scalar()
    assert trig == 1, "updated_at trigger missing -> migrations did not run"

    partial = db_connection.execute(
        text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_pipeline_runs_running'")
    ).scalar()
    assert partial == 1, "partial index missing -> migrations did not run"


def test_isolation_insert(db_session):
    """Insert a sentinel row; the NEXT test must not see it."""
    db_session.add(Property(property_id="ISO-SENTINEL"))
    db_session.flush()
    count = db_session.query(Property).filter_by(property_id="ISO-SENTINEL").count()
    assert count == 1


def test_isolation_rolled_back(db_session):
    """The sentinel from the previous test is gone -> transaction rollback works."""
    count = db_session.query(Property).filter_by(property_id="ISO-SENTINEL").count()
    assert count == 0
