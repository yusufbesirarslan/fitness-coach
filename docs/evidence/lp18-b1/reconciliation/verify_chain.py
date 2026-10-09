import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
sys.path.insert(0, str(Path.cwd() / 'tests'))
import conftest
import sqlalchemy as sa
from flask import Flask
from flask_migrate import Migrate, upgrade
from app.extensions import db
from test_lp18_b1_migration import migration as b1
from test_lp18_b0_migration import migration as b0
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from app.models import TrainingPlanReplacementGenerationOperation
import os
url=os.environ['PG_TEST_DATABASE_URL']
app=Flask('lp18-chain');app.config['SQLALCHEMY_DATABASE_URI']=url
db.init_app(app);Migrate(app,db,directory=str(Path.cwd()/'migrations'))
with app.app_context():
    for start in ['clean','e3f4a5b6c7d8']:
        with db.engine.begin() as c:
            c.execute(sa.text('DROP SCHEMA public CASCADE'));c.execute(sa.text('CREATE SCHEMA public'))
        if start!='clean':
            upgrade(revision=start)
            with db.engine.connect() as c:
                assert c.execute(sa.text('select version_num from alembic_version')).scalar_one()==start
                assert b1().TABLE not in sa.inspect(c).get_table_names()
                before={t:sa.inspect(c).get_columns(t) for t in b0().SCHEMA}
        upgrade()
        upgrade()
        with db.engine.begin() as c:
            assert c.execute(sa.text('select version_num from alembic_version')).scalar_one()=='b1a8c9d0e1f2'
            with Operations.context(MigrationContext.configure(c)):
                b0().upgrade();b1().upgrade()
            assert set(b1().SCHEMA[b1().TABLE]['columns'])==set(TrainingPlanReplacementGenerationOperation.__table__.columns.keys())
            if start!='clean':
                for t,cols in before.items():
                    assert [(x['name'],str(x['type']),x['nullable'],x['default']) for x in cols]==[(x['name'],str(x['type']),x['nullable'],x['default']) for x in sa.inspect(c).get_columns(t)]
        print(f'{start}: full Alembic upgrade, rerun, B0/B1 verifiers and model column parity PASS')
    with db.engine.begin() as c:
        c.execute(sa.text('DROP SCHEMA public CASCADE'));c.execute(sa.text('CREATE SCHEMA public'))
