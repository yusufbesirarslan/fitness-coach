"""The LP-11 deleted-identity tombstone migration: additive and reversible."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import DeletedIdentityTombstone


MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
             "d0e1f2a3b4c5_add_deleted_identity_tombstone.py")
TABLE = "deleted_identity_tombstone"
FINGERPRINT = "ab" * 32


def _migration():
    spec = importlib.util.spec_from_file_location(
        "deleted_identity_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


def test_upgrade_on_an_existing_database_is_additive_and_downgrade_reverses(
        tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'existing.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(
                "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT)"))
            connection.execute(sa.text(
                "INSERT INTO user (id, username) VALUES (1, 'kept')"))
            migration = _migration()
            _run(connection, migration.upgrade)

            inspector = sa.inspect(connection)
            assert {c["name"] for c in inspector.get_columns(TABLE)} == {
                "fingerprint", "deleted_at"}
            assert inspector.get_pk_constraint(TABLE)[
                "constrained_columns"] == ["fingerprint"]
            connection.execute(sa.text(
                f"INSERT INTO {TABLE} (fingerprint, deleted_at) "
                "VALUES (:f, CURRENT_TIMESTAMP)"), {"f": FINGERPRINT})
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(sa.text(
                        f"INSERT INTO {TABLE} (fingerprint, deleted_at) "
                        "VALUES (:f, CURRENT_TIMESTAMP)"), {"f": FINGERPRINT})

            _run(connection, migration.upgrade)          # idempotent re-run
            _run(connection, migration.downgrade)
            assert not sa.inspect(connection).has_table(TABLE)
            assert connection.execute(sa.text(
                "SELECT username FROM user WHERE id = 1")).scalar_one() == "kept"
    finally:
        engine.dispose()


def test_upgrade_on_a_fresh_database_creates_the_table(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    try:
        with engine.begin() as connection:
            _run(connection, _migration().upgrade)
            assert sa.inspect(connection).has_table(TABLE)
            _run(connection, _migration().downgrade)
            assert not sa.inspect(connection).has_table(TABLE)
    finally:
        engine.dispose()


def test_upgrade_is_safe_after_create_all_and_matches_the_model(app):
    with app.app_context():
        with db.engine.begin() as connection:
            _run(connection, _migration().upgrade)
            columns = {c["name"] for c in
                       sa.inspect(connection).get_columns(TABLE)}
    assert columns == {c.name for c in DeletedIdentityTombstone.__table__.columns}


def test_upgrade_refuses_a_foreign_table_under_the_same_name(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'foreign.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(
                f"CREATE TABLE {TABLE} (fingerprint TEXT PRIMARY KEY, "
                "deleted_at TIMESTAMP, email TEXT)"))
            with pytest.raises(RuntimeError):
                _run(connection, _migration().upgrade)
    finally:
        engine.dispose()
