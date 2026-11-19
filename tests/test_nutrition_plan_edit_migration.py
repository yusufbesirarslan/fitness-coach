"""Expand compatibility, drift refusal and history-preserving rollback."""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/c1a9b7e5d301_nutrition_edit_history.py"
    spec = importlib.util.spec_from_file_location("c1_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_schema(conn):
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
    conn.execute(sa.text('CREATE TABLE nutrition_plan (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, plan_data TEXT NOT NULL)'))
    conn.execute(sa.text('INSERT INTO "user" (id) VALUES (1)'))
    conn.execute(sa.text('INSERT INTO nutrition_plan (id,user_id,plan_data) VALUES (1,1,:raw),(2,1,:raw)'), {"raw": ' { "exact": "old bytes" } '})


def test_backfill_rerun_old_writer_and_empty_downgrade():
    m = migration(); engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        old_schema(conn)
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade()
            rows = conn.execute(sa.text("SELECT lineage_id,mutation_version,plan_data FROM nutrition_plan ORDER BY id")).all()
            assert len({x[0] for x in rows}) == 2 and all(len(x[0]) >= 32 for x in rows)
            assert all(x[1:] == (0, ' { "exact": "old bytes" } ') for x in rows)
            m.upgrade()
            assert rows == conn.execute(sa.text("SELECT lineage_id,mutation_version,plan_data FROM nutrition_plan ORDER BY id")).all()
            # Previous serving revision knows neither new column.
            conn.execute(sa.text("INSERT INTO nutrition_plan (id,user_id,plan_data) VALUES (3,1,'old writer')"))
            assert conn.execute(sa.text("SELECT lineage_id,mutation_version FROM nutrition_plan WHERE id=3")).one() == (None, 0)
            m.downgrade()
            assert conn.execute(sa.text("SELECT plan_data FROM nutrition_plan WHERE id=1")).scalar_one() == ' { "exact": "old bytes" } '


def test_create_all_before_upgrade_parity(app):
    from app.extensions import db
    with Operations.context(MigrationContext.configure(db.session.connection())):
        migration().upgrade()
        migration().upgrade()
    db.session.rollback()


@pytest.mark.parametrize("damage", ["column", "unique", "predicate", "fk"])
def test_authority_drift_is_refused(damage):
    m = migration(); engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        old_schema(conn)
        with Operations.context(MigrationContext.configure(conn)): m.upgrade()
        table = "nutrition_plan_edit_operation"
        if damage == "column":
            conn.execute(sa.text("ALTER TABLE nutrition_plan_edit_operation ADD COLUMN wrong INTEGER"))
        elif damage == "predicate":
            conn.execute(sa.text("DROP INDEX uq_nutrition_edit_active_owner"))
            conn.execute(sa.text("CREATE UNIQUE INDEX uq_nutrition_edit_active_owner ON nutrition_plan_edit_operation(user_id) WHERE status='applied'"))
        else:
            ddl = conn.execute(sa.text("SELECT sql FROM sqlite_master WHERE name='nutrition_plan_edit_operation'")).scalar_one()
            conn.execute(sa.text("DROP TABLE nutrition_plan_edit_operation"))
            if damage == "unique": ddl = ddl.replace("UNIQUE (user_id, idempotency_key)", "UNIQUE (user_id, command_fingerprint)")
            else: ddl = ddl.replace("ON DELETE CASCADE", "ON DELETE RESTRICT")
            conn.execute(sa.text(ddl))
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="schema mismatch"): m.upgrade()


def test_nonempty_history_refuses_destructive_downgrade(app, make_user):
    from app.extensions import db
    from app.models import NutritionPlanEditOperation
    owner = make_user("c1-migration").id
    db.session.add(NutritionPlanEditOperation(user_id=owner, idempotency_key="c1-key-0001",
        command_fingerprint="a"*64, command_payload={}, plan_lineage_id="lineage",
        base_version=0, before_snapshot="exact", status="failed"))
    db.session.commit()
    with Operations.context(MigrationContext.configure(db.session.connection())):
        with pytest.raises(RuntimeError, match="retain schema"): migration().downgrade()
    assert NutritionPlanEditOperation.query.count() == 1
