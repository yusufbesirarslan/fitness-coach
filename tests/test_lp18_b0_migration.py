"""Expand-only schema, rerun verification, one Alembic head."""
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT=Path(__file__).resolve().parents[1]


def migration():
    spec=importlib.util.spec_from_file_location('lp18_migration', ROOT/'migrations/versions/e3f4a5b6c7d8_replacement_authority.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_upgrade_and_rerun_one_head():
    m=migration(); engine=sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade();m.upgrade()
        inspector=sa.inspect(conn)
        for name in m.SCHEMA:
            assert {c['name'] for c in inspector.get_columns(name)} == set(m.SCHEMA[name]['columns'])
            assert all(f['referred_table']=='user' for f in inspector.get_foreign_keys(name))
        assert len(inspector.get_check_constraints('training_plan_replacement_receipt')) == 2
    config=Config();config.set_main_option('script_location', str(ROOT/'migrations'))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected one head, found {heads}"
    assert m.revision == "e3f4a5b6c7d8"
    assert m.down_revision == "e2f3a4b5c6d7"
    assert heads == [m.revision]
    assert m.expand_contract=='expand'


def test_new_migration_is_additive():
    import ast
    tree=ast.parse((ROOT/'migrations/versions/e3f4a5b6c7d8_replacement_authority.py').read_text())
    upgrade=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='upgrade')
    operations={n.func.attr for n in ast.walk(upgrade) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='op'}
    assert operations=={'create_table','create_index','get_bind'}


def test_damaged_existing_schema_fails_closed():
    m=migration();engine=sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        conn.execute(sa.text('CREATE TABLE training_plan_replacement_proposal (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match='columns mismatch'):m.upgrade()


def test_downgrade_refuses_to_erase_durable_replacement_authority():
    m = migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE user (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            m.upgrade()
            with pytest.raises(RuntimeError, match="expand-only replacement authority"):
                m.downgrade()
        assert set(m.SCHEMA) <= set(sa.inspect(conn).get_table_names())
