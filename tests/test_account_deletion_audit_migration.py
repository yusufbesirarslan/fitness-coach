"""The destruction-record migration: one additive table, verify-or-create."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

from app.extensions import db
from app.models import AccountDeletionRecord


MIGRATION = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
             "ad17de1e0001_account_deletion_record.py")
TABLE = "account_deletion_record"
INSERT = sa.text(
    f"INSERT INTO {TABLE} (operation_ref, channel, outcome, identity_step, "
    "scope, counts, completed_at, retain_until, integrity) VALUES "
    "(:ref, :channel, 'completed', 'deleted', 'full_account', '{}', "
    "'2026-11-14 10:00:00', :until, :mac)")
ROW = {"ref": "ab" * 16, "channel": "self_service",
       "until": "2029-11-14 10:00:00", "mac": "cd" * 32}


def _migration():
    spec = importlib.util.spec_from_file_location("deletion_record_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


def test_revision_identity():
    migration = _migration()
    assert migration.revision == "ad17de1e0001"
    assert migration.down_revision == "f01a4e000003"
    assert migration.expand_contract == "expand"


def test_upgrade_is_additive_idempotent_and_enforces_its_constraints(tmp_path):
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
                c.name for c in AccountDeletionRecord.__table__.columns}
            assert inspector.get_foreign_keys(TABLE) == []
            connection.execute(INSERT, ROW)
            for bad in ({**ROW, "ref": "ab" * 16, "mac": "ee" * 32},        # duplicate ref
                        {**ROW, "ref": "ac" * 16, "channel": "support"},    # vocabulary
                        {**ROW, "ref": "ad" * 16,
                         "until": "2026-11-14 10:00:00"}):                  # no retention
                with pytest.raises(sa.exc.IntegrityError):
                    with connection.begin_nested():
                        connection.execute(INSERT, bad)
            _run(connection, migration.upgrade)                             # re-run
            assert connection.execute(sa.text(
                f"SELECT count(*) FROM {TABLE}")).scalar_one() == 1
            assert connection.execute(sa.text(
                "SELECT username FROM user WHERE id = 1")).scalar_one() == "kept"
    finally:
        engine.dispose()


def test_downgrade_refuses_to_drop_records_and_drops_an_empty_table(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'down.db'}")
    try:
        with engine.begin() as connection:
            migration = _migration()
            _run(connection, migration.upgrade)
            connection.execute(INSERT, ROW)
            with pytest.raises(RuntimeError, match="three years"):
                _run(connection, migration.downgrade)
            assert sa.inspect(connection).has_table(TABLE)
            connection.execute(sa.text(f"DELETE FROM {TABLE}"))
            _run(connection, migration.downgrade)
            assert not sa.inspect(connection).has_table(TABLE)
            _run(connection, migration.downgrade)                           # absent: no-op
    finally:
        engine.dispose()


def test_upgrade_after_create_all_accepts_the_model_table(app):
    with app.app_context():
        with db.engine.begin() as connection:
            _run(connection, _migration().upgrade)
            assert sa.inspect(connection).has_table(TABLE)


@pytest.mark.parametrize("ddl", [
    f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, note TEXT)",
    # Right columns, but tied to the account it must outlive.
    f"CREATE TABLE {TABLE} (id INTEGER PRIMARY KEY, "
    "operation_ref VARCHAR(32) NOT NULL UNIQUE, request_id VARCHAR(80), "
    "channel VARCHAR(24) NOT NULL, outcome VARCHAR(24) NOT NULL, "
    "identity_step VARCHAR(24) NOT NULL, subject_fingerprint VARCHAR(64), "
    "scope VARCHAR(24) NOT NULL, counts JSON NOT NULL, "
    "completed_at DATETIME NOT NULL, retain_until DATETIME NOT NULL, "
    "integrity VARCHAR(64) NOT NULL REFERENCES user (id) ON DELETE CASCADE)",
])
def test_upgrade_refuses_a_foreign_table_under_the_same_name(tmp_path, ddl):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'foreign.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
            connection.execute(sa.text(ddl))
            with pytest.raises(RuntimeError):
                _run(connection, _migration().upgrade)
    finally:
        engine.dispose()
