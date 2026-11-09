"""First-party health consent. Empty additive tables; no inferred permission.

Revision ID: f01c0a1e0001
Revises: d8f1a3c5e7b9
No customer backfill, inferred grants or modifications to #436 migrations.
"""
from alembic import op
import sqlalchemy as sa
import re

revision = 'f01c0a1e0001'
down_revision = 'd8f1a3c5e7b9'
branch_labels = None
depends_on = None
expand_contract = "expand"


def _tables():
    metadata = sa.MetaData()
    sa.Table('user', metadata, sa.Column('id', sa.Integer, primary_key=True))
    state = sa.Table('health_consent_state', metadata,
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('generation', sa.BigInteger, nullable=False),
        sa.Column('record', sa.JSON, nullable=False),
        sa.Column('changed_at', sa.DateTime, nullable=False),
        sa.CheckConstraint('generation > 0', name='ck_health_consent_generation'))
    event = sa.Table('health_consent_event', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('generation', sa.BigInteger, nullable=False),
        sa.Column('purpose', sa.String(80), nullable=False),
        sa.Column('version', sa.String(80), nullable=False),
        sa.Column('locale', sa.String(8), nullable=False),
        sa.Column('text_digest', sa.String(64), nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('occurred_at', sa.DateTime, nullable=False),
        sa.Column('request_id', sa.String(80), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.UniqueConstraint('owner_id', 'request_id', name='uq_health_consent_request'),
        sa.UniqueConstraint('owner_id', 'generation', name='uq_health_consent_generation'),
        sa.CheckConstraint('generation > 0', name='ck_health_event_generation'),
        sa.CheckConstraint("action IN ('affirmative_grant', 'decline', 'withdraw')", name='ck_health_event_action'))
    return state, event


def _predicate(value):
    value = re.sub(r"::(?:character varying|text)(?:\[\])?", "", value or "")
    return re.sub(r'[\s()"]', '', value).lower()


def upgrade():
    bind = op.get_bind()
    for table in _tables():
        inspector = sa.inspect(bind)
        if not inspector.has_table(table.name):
            table.create(bind)
            continue
        # The baseline/current create_all boot can already include these
        # tables. Preserve only a fully equivalent schema; never repair or
        # reinterpret a partial authority table as valid consent.
        columns = {c['name']: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError('Health consent table has incompatible columns')
        for column in table.c:
            actual = columns[column.name]
            if (actual['nullable'] != column.nullable
                    or actual['type']._type_affinity != column.type._type_affinity
                    or (isinstance(column.type, sa.String) and actual['type'].length != column.type.length)):
                raise RuntimeError('Health consent table has incompatible types')
        if inspector.get_pk_constraint(table.name)['constrained_columns'] != [c.name for c in table.primary_key]:
            raise RuntimeError('Health consent table lacks owner/evidence identity')
        foreign = inspector.get_foreign_keys(table.name)
        if not any(f['constrained_columns'] == ['owner_id'] and f['referred_table'] == 'user'
                   and f['referred_columns'] == ['id'] and f.get('options', {}).get('ondelete') == 'CASCADE'
                   for f in foreign):
            raise RuntimeError('Health consent table lacks account deletion fence')
        unique = {tuple(c['column_names']) for c in inspector.get_unique_constraints(table.name)}
        required = {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                    if isinstance(constraint, sa.UniqueConstraint)}
        if not required <= unique:
            raise RuntimeError('Health consent table lacks idempotency constraints')
        checks = {c['name']: _predicate(c.get('sqltext')) for c in inspector.get_check_constraints(table.name)}
        required_checks = [c for c in table.constraints if isinstance(c, sa.CheckConstraint)]
        for constraint in required_checks:
            accepted = {_predicate(str(constraint.sqltext))}
            if constraint.name == 'ck_health_event_action':
                accepted.add("action=anyarray['affirmative_grant','decline','withdraw']")
            if checks.get(constraint.name) not in accepted:
                raise RuntimeError('Health consent table has incompatible checks')
        if bind.dialect.name == 'postgresql':
            states = dict(bind.execute(sa.text(
                "SELECT conname, convalidated FROM pg_catalog.pg_constraint "
                "WHERE conrelid = pg_catalog.to_regclass(:relation) AND contype = 'c'"
            ), {'relation': table.name}).all())
            if any(states.get(c.name) is not True for c in required_checks):
                raise RuntimeError('Health consent checks are not validated')


def downgrade():
    connection = op.get_bind()
    for table in ('health_consent_event', 'health_consent_state'):
        if connection.execute(sa.text('SELECT 1 FROM ' + table + ' LIMIT 1')).first():
            raise RuntimeError('Health consent downgrade refuses to erase consent evidence; retain schema on rollback')
    op.drop_table('health_consent_event')
    op.drop_table('health_consent_state')
