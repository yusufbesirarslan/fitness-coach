"""Migration a9c1d2e3f4b5 (MON-02-BE-03A): additive, reentrant, fail-closed."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("ai_starter_grant", "ai_allowance_bucket", "ai_usage_reservation")


def migration():
    path = ROOT / "migrations/versions/a9c1d2e3f4b5_ai_allowance_ledger.py"
    spec = importlib.util.spec_from_file_location("ai_allowance_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def base(conn):
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY, is_premium BOOLEAN, user_metadata TEXT)'))
    conn.execute(sa.text("""INSERT INTO "user" VALUES (1, true, '{"ai_plan_quota": {"chat": 3}}')"""))


def test_descends_from_the_subscription_foundation():
    m = migration()
    assert (m.revision, m.down_revision, m.expand_contract) == ("a9c1d2e3f4b5", "c2b9d0e1f2a3", "expand")


def test_roundtrip_is_additive_and_writes_no_rows(tmp_path):
    m = migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    with engine.begin() as conn:
        base(conn)
        run(conn, m.upgrade)
        run(conn, m.upgrade)  # reentrant
        for table in TABLES:
            assert conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() == 0
        assert conn.execute(sa.text('SELECT is_premium, user_metadata FROM "user"')).one() == (
            1, '{"ai_plan_quota": {"chat": 3}}')
        run(conn, m.downgrade)
        assert not any(sa.inspect(conn).has_table(t) for t in TABLES)
        run(conn, m.upgrade)
        assert all(sa.inspect(conn).has_table(t) for t in TABLES)


def test_create_all_first_boot_is_accepted(tmp_path):
    from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    with engine.begin() as conn:
        base(conn)
        for model in (AiStarterGrant, AiAllowanceBucket, AiUsageReservation):
            model.__table__.create(conn)
        run(conn, migration().upgrade)


def _damaged(conn, table_name, damage):
    m = migration()
    tables = {t.name: t for t in m._tables()}
    table = tables[table_name]
    if damage == "check":
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint) and constraint.name in (
                    "ck_ai_allowance_bucket_within", "ck_ai_usage_reservation_state"):
                table.constraints.remove(constraint)
                table.append_constraint(sa.CheckConstraint("1=1", name=constraint.name))
    elif damage == "missing_check":
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint):
                table.constraints.remove(constraint)
                break
    elif damage == "unique":
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.UniqueConstraint):
                table.constraints.remove(constraint)
    elif damage == "cascade":
        for constraint in table.foreign_key_constraints:
            constraint.ondelete = None
            for fk in constraint.elements:
                fk.ondelete = None
    elif damage == "column":
        table._columns.remove(table.c.outcome if table_name == "ai_usage_reservation" else table.c.policy_version)
    for name in TABLES:
        if name == table_name:
            break
        tables[name].create(conn)
    table.create(conn)
    return m


DAMAGE = [(table, damage) for table in TABLES
          for damage in ("check", "missing_check", "unique", "cascade", "column")
          if not (table == "ai_starter_grant" and damage in ("check", "missing_check", "unique"))]


@pytest.mark.parametrize("table_name,damage", DAMAGE)
def test_incompatible_existing_schema_fails_closed(tmp_path, table_name, damage):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'damaged.db'}")
    with engine.begin() as conn:
        base(conn)
        m = _damaged(conn, table_name, damage)
        with pytest.raises(RuntimeError):
            run(conn, m.upgrade)
        assert conn.execute(sa.text('SELECT id FROM "user"')).scalar() == 1


def test_downgrade_refuses_populated_ledger_without_losing_rows(tmp_path):
    m = migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'populated.db'}")
    with engine.begin() as conn:
        base(conn)
        run(conn, m.upgrade)
        conn.execute(sa.text("INSERT INTO ai_starter_grant VALUES (1, 'test-v1', CURRENT_TIMESTAMP)"))
        with pytest.raises(RuntimeError, match="refuses populated"):
            run(conn, m.downgrade)
        assert all(sa.inspect(conn).has_table(t) for t in TABLES)
        assert conn.execute(sa.text("SELECT count(*) FROM ai_starter_grant")).scalar() == 1
