"""Additive migration, model boot parity and fail-closed schema drift."""
import importlib.util
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config
from app.models import TrainingPlanReplacementGenerationOperation as Operation
ROOT=Path(__file__).resolve().parents[1]


def migration():
    spec=importlib.util.spec_from_file_location('b1_migration',ROOT/'migrations/versions/b1a8c9d0e1f2_replacement_generation.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_upgrade_rerun_and_model_shape():
    m=migration();e=sa.create_engine('sqlite://')
    with e.begin() as c:
        c.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(c)):m.upgrade();m.upgrade()
        assert set(m.SCHEMA[m.TABLE]['columns'])==set(Operation.__table__.columns.keys())
        assert m.expand_contract=='expand'
        with pytest.raises(RuntimeError):m.downgrade()
    cfg=Config();cfg.set_main_option('script_location',str(ROOT/'migrations'))
    assert ScriptDirectory.from_config(cfg).get_heads()==['b1a8c9d0e1f2']


@pytest.mark.parametrize('damage',['column','unique','check','predicate','fk'])
def test_schema_drift_refused(damage):
    m=migration();e=sa.create_engine('sqlite://')
    with e.begin() as c:
        c.execute(sa.text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(c)):m.upgrade()
        if damage=='predicate':
            c.execute(sa.text('DROP INDEX uq_replacement_generation_active_owner'))
            c.execute(sa.text("CREATE UNIQUE INDEX uq_replacement_generation_active_owner ON training_plan_replacement_generation_operation(user_id) WHERE status='SUCCEEDED'"))
        elif damage=='column':c.execute(sa.text('ALTER TABLE training_plan_replacement_generation_operation ADD COLUMN extraneous INTEGER'))
        else:
            # SQLite table rebuild intentionally damages a same-name authority.
            sql=c.execute(sa.text("SELECT sql FROM sqlite_master WHERE name='training_plan_replacement_generation_operation'")).scalar_one()
            c.execute(sa.text('DROP TABLE training_plan_replacement_generation_operation'))
            if damage=='unique':sql=sql.replace('UNIQUE (user_id, key_digest)','UNIQUE (user_id, intent_fingerprint)')
            elif damage=='check':sql=sql.replace('attempt_count <= 2','attempt_count <= 20')
            else:sql=sql.replace('ON DELETE CASCADE','ON DELETE RESTRICT')
            c.execute(sa.text(sql))
            c.execute(sa.text("CREATE UNIQUE INDEX uq_replacement_generation_active_owner ON training_plan_replacement_generation_operation(user_id) WHERE status='IN_PROGRESS'"))
        with Operations.context(MigrationContext.configure(c)):
            with pytest.raises(RuntimeError):m.upgrade()
