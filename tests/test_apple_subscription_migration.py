import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/c2b9d0e1f2a3_apple_subscription_foundation.py"
    spec = importlib.util.spec_from_file_location("apple_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_additive_migration_roundtrip_and_create_all_boot(tmp_path):
    m = migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, is_premium BOOLEAN)'))
        conn.execute(sa.text('INSERT INTO "user" VALUES (1, true)'))
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade(); m.upgrade()
            assert conn.execute(sa.text('SELECT is_premium FROM "user"')).scalar() == 1
            assert conn.execute(sa.text('SELECT count(*) FROM apple_subscription')).scalar() == 0
            m.downgrade(); m.upgrade()
        assert sa.inspect(conn).has_table("apple_subscription")


def test_migration_accepts_models_and_rejects_broken_schema(tmp_path):
    from app.models import AppleAccountAssociation, AppleSubscription
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'model.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
        AppleAccountAssociation.__table__.create(conn); AppleSubscription.__table__.create(conn)
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()
    broken = sa.create_engine(f"sqlite:///{tmp_path / 'broken.db'}")
    with broken.begin() as conn:
        conn.execute(sa.text('CREATE TABLE apple_account_association (user_id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            migration().upgrade()
