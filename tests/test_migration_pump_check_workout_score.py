from importlib import util
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa


def _load_migration():
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "b1c2d3e4f5a6_add_pump_check_workout_score.py"
    spec = util.spec_from_file_location("pump_check_workout_score_migration", path)
    module = util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_upgrade_and_downgrade_use_alembic_ops_for_non_postgresql(monkeypatch):
    migration = _load_migration()
    calls = []

    class DummyBind:
        dialect = SimpleNamespace(name="sqlite")

    class DummyBatch:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_column(self, column):
            calls.append(("add", column.name, isinstance(column.type, sa.Float), column.nullable))

        def drop_column(self, column_name):
            calls.append(("drop", column_name))

    monkeypatch.setattr(migration.op, "get_bind", lambda: DummyBind())
    monkeypatch.setattr(migration.op, "batch_alter_table", lambda *args, **kwargs: DummyBatch())

    migration.upgrade()
    migration.downgrade()

    assert calls == [("add", "workout_score", True, True), ("drop", "workout_score")]


def test_upgrade_and_downgrade_keep_postgresql_sql(monkeypatch):
    migration = _load_migration()
    statements = []

    class DummyBind:
        dialect = SimpleNamespace(name="postgresql")

    monkeypatch.setattr(migration.op, "get_bind", lambda: DummyBind())
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(statement))

    migration.upgrade()
    migration.downgrade()

    assert statements == [
        "ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS workout_score DOUBLE PRECISION",
        "ALTER TABLE pump_check DROP COLUMN IF EXISTS workout_score",
    ]
