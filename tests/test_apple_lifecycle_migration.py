import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

TABLES = ("apple_notification_receipt", "apple_subscription_reconciliation")


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/a4d6f8b0c2e1_apple_lifecycle_reconciliation.py"
    spec = importlib.util.spec_from_file_location("apple_lifecycle_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def _user_table(conn):
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, is_premium BOOLEAN)'))
    conn.execute(sa.text('INSERT INTO "user" VALUES (1, true)'))


def test_additive_roundtrip_reentrant_and_no_user_change(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        _user_table(conn)
        with Operations.context(MigrationContext.configure(conn)):
            m = migration()
            m.upgrade(); m.upgrade()
            assert all(conn.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar() == 0 for t in TABLES)
            m.downgrade()
            assert not any(sa.inspect(conn).has_table(t) for t in TABLES)
            m.upgrade()
        assert conn.execute(sa.text('SELECT id, is_premium FROM "user"')).all() == [(1, 1)]


def test_create_all_first_boot_accepted(tmp_path):
    from app.models import AppleNotificationReceipt, AppleSubscriptionReconciliation
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    with engine.begin() as conn:
        _user_table(conn)
        AppleNotificationReceipt.__table__.create(conn)
        AppleSubscriptionReconciliation.__table__.create(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()


BROKEN = [
    "CREATE TABLE apple_notification_receipt (notification_uuid VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE apple_subscription_reconciliation (environment VARCHAR(10) NOT NULL, "
    "original_transaction_id VARCHAR(64) NOT NULL, account_token VARCHAR(36), generation INTEGER NOT NULL, "
    "attempts INTEGER NOT NULL, next_attempt_at DATETIME NOT NULL, lease_until DATETIME, "
    "last_outcome VARCHAR(40), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
    "CONSTRAINT ck_apple_reconciliation_environment CHECK (environment IN ('Sandbox', 'Production', 'Xcode')), "
    "PRIMARY KEY (environment, original_transaction_id))",
    "CREATE TABLE apple_subscription_reconciliation (environment VARCHAR(10) NOT NULL, "
    "original_transaction_id VARCHAR(64) NOT NULL, account_token VARCHAR(36), generation INTEGER NOT NULL, "
    "attempts INTEGER NOT NULL, next_attempt_at DATETIME NOT NULL, lease_until DATETIME, "
    "last_outcome VARCHAR(40), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
    "CONSTRAINT ck_apple_reconciliation_environment CHECK (environment IN ('Sandbox', 'Production')), "
    "PRIMARY KEY (original_transaction_id))",
]


@pytest.mark.parametrize("ddl", BROKEN)
def test_incompatible_existing_schema_rejected_without_changes(tmp_path, ddl):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'broken.db'}")
    with engine.begin() as conn:
        _user_table(conn)
        conn.execute(sa.text(ddl))
        before = set(sa.inspect(conn).get_table_names())
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            migration().upgrade()
        assert set(sa.inspect(conn).get_table_names()) == before


def test_single_head_after_be01():
    m = migration()
    assert (m.down_revision, m.expand_contract) == ("d7e3a9c1f5b8", "expand")
