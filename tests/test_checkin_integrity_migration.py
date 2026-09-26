"""The submission-key migration preserves legacy rows and is reversible."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db


MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
             "c8d9e0f1a2b3_checkin_submission_key.py")


def _migration():
    spec = importlib.util.spec_from_file_location("checkin_key_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


def test_upgrade_adds_unique_owner_key_and_downgrade_preserves_legacy_rows(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'checkin-legacy.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("""
                CREATE TABLE weekly_check_in (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                    weight FLOAT NOT NULL)
            """))
            connection.execute(sa.text(
                "INSERT INTO weekly_check_in (id,user_id,weight) VALUES (1,7,79)"))
            migration = _migration()
            _run(connection, migration.upgrade)
            columns = {c["name"] for c in sa.inspect(connection).get_columns("weekly_check_in")}
            assert {"idempotency_key", "request_fingerprint", "response_snapshot"} <= columns
            connection.execute(sa.text("""
                INSERT INTO weekly_check_in (id,user_id,weight,idempotency_key)
                VALUES (2,7,78,'same-attempt')
            """))
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(sa.text("""
                        INSERT INTO weekly_check_in (id,user_id,weight,idempotency_key)
                        VALUES (3,7,78,'same-attempt')
                    """))
            connection.execute(sa.text("""
                INSERT INTO weekly_check_in (id,user_id,weight,idempotency_key)
                VALUES (4,8,78,'same-attempt')
            """))
            _run(connection, migration.downgrade)
            assert "idempotency_key" not in {
                c["name"] for c in sa.inspect(connection).get_columns("weekly_check_in")}
            assert connection.execute(sa.text(
                "SELECT weight FROM weekly_check_in WHERE id=1")).scalar_one() == 79
    finally:
        engine.dispose()


def test_upgrade_is_safe_after_create_all(app):
    with app.app_context():
        with db.engine.begin() as connection:
            _run(connection, _migration().upgrade)
            assert any(c["name"] == "idempotency_key" for c in
                       sa.inspect(connection).get_columns("weekly_check_in"))
