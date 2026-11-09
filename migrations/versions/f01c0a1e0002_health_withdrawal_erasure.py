"""F01 withdrawal erasure (A1 = O2-B). Two empty additive tables; no backfill.

Revision ID: f01c0a1e0002
Revises: e1a5f0c3d2b4
`health_erasure` records owed/completed erasures (ids, times, counts; no account
foreign key, so the record outlives the account). `health_legal_hold` holds the
controller's documented exemptions and cascades with the account.
"""
from alembic import op
import sqlalchemy as sa
import re

revision = 'f01c0a1e0002'
down_revision = 'e1a5f0c3d2b4'
branch_labels = None
depends_on = None
expand_contract = "expand"


def _tables():
    metadata = sa.MetaData()
    sa.Table('user', metadata, sa.Column('id', sa.Integer, primary_key=True))
    erasure = sa.Table('health_erasure', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_id', sa.Integer, nullable=False),
        sa.Column('generation', sa.BigInteger, nullable=False),
        sa.Column('requested_at', sa.DateTime, nullable=False),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('summary', sa.JSON, nullable=True),
        sa.UniqueConstraint('owner_id', 'generation', name='uq_health_erasure_generation'),
        sa.CheckConstraint('generation > 0', name='ck_health_erasure_generation'))
    hold = sa.Table('health_legal_hold', metadata,
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('categories', sa.JSON, nullable=False),
        sa.Column('matter_ref', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('review_at', sa.DateTime, nullable=False),
        sa.Column('released_at', sa.DateTime, nullable=True))
    return erasure, hold


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
        # A create_all-first boot may already include these tables. Accept only
        # a fully equivalent schema; never repair a partial one.
        columns = {c['name']: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError('Health erasure table has incompatible columns')
        for column in table.c:
            actual = columns[column.name]
            if (actual['nullable'] != column.nullable
                    or actual['type']._type_affinity != column.type._type_affinity
                    or (isinstance(column.type, sa.String) and actual['type'].length != column.type.length)):
                raise RuntimeError('Health erasure table has incompatible types')
        if inspector.get_pk_constraint(table.name)['constrained_columns'] != ['id']:
            raise RuntimeError('Health erasure table lacks its identity')
        foreign = inspector.get_foreign_keys(table.name)
        if not table.foreign_keys:
            if foreign:
                raise RuntimeError('Health erasure record must not cascade with the account')
        elif not any(f['constrained_columns'] == ['owner_id'] and f['referred_table'] == 'user'
                     and f['referred_columns'] == ['id'] and f.get('options', {}).get('ondelete') == 'CASCADE'
                     for f in foreign):
            raise RuntimeError('Health legal hold lacks account deletion fence')
        unique = {tuple(c['column_names']) for c in inspector.get_unique_constraints(table.name)}
        required = {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                    if isinstance(constraint, sa.UniqueConstraint)}
        if not required <= unique:
            raise RuntimeError('Health erasure table lacks idempotency constraints')
        checks = {c['name']: _predicate(c.get('sqltext')) for c in inspector.get_check_constraints(table.name)}
        for constraint in (c for c in table.constraints if isinstance(c, sa.CheckConstraint)):
            if checks.get(constraint.name) != _predicate(str(constraint.sqltext)):
                raise RuntimeError('Health erasure table has incompatible checks')


def downgrade():
    connection = op.get_bind()
    tables = ('health_legal_hold', 'health_erasure')
    for table in tables:
        if connection.execute(sa.text('SELECT 1 FROM ' + table + ' LIMIT 1')).first():
            raise RuntimeError('Health erasure downgrade refuses to drop erasure evidence; retain schema on rollback')
    for table in tables:
        op.drop_table(table)
