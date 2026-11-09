"""O2-B withdrawal erasure; preserves the merged F01 revision. Empty additive tables; no inferred permission.

Revision ID: f01e2a5e0002
Revises: e1a5f0c3d2b4
No customer backfill, inferred grants or modifications to #436 migrations.
"""
from alembic import op
import sqlalchemy as sa
import re
import importlib.util
from pathlib import Path

revision = 'f01e2a5e0002'
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



# Only the retention exception changes. Admission, floor, binding guards,
# immutability, truncate protection and every ENABLE ALWAYS trigger stay exact.
RETENTION_BODY = """
BEGIN
    IF EXISTS (SELECT 1 FROM public."user" u WHERE u.id = OLD.user_id)
       AND NOT (
           EXISTS (SELECT 1 FROM public.health_erasure e
                   WHERE e.owner_id = OLD.user_id AND e.completed_at IS NULL)
           AND EXISTS (SELECT 1 FROM public.health_consent_state s
                       WHERE s.owner_id = OLD.user_id
                         AND s.record->>'state' IN ('withdrawn', 'declined'))
           AND NOT EXISTS (SELECT 1 FROM public.health_legal_hold h
                           WHERE h.owner_id = OLD.user_id AND h.released_at IS NULL
                             AND h.categories::jsonb ? 'workouts')
       ) THEN
        RAISE EXCEPTION 'completion claims are retained without a stopped health erasure'
            USING ERRCODE = 'AXF05';
    END IF;
    RETURN NULL;
END
"""


def _base_fence():
    # Fresh module: never mutate the original revision's cached expectations.
    path = Path(__file__).with_name('d7e3a9c1f5b8_completion_authority_fence.py')
    spec = importlib.util.spec_from_file_location('_o2b_base_fence', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_fence(bind):
    """Strict verification of either reviewed schema's complete fence."""
    base = _base_fence()
    source = bind.execute(sa.text(
        "SELECT p.prosrc FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND p.proname='completion_fence_claim_retention' "
        "AND p.pronargs=0")).scalar()
    if source == RETENTION_BODY:
        for table in ('health_erasure', 'health_consent_state', 'health_legal_hold'):
            if not sa.inspect(bind).has_table(table):
                raise RuntimeError('Health erasure fence lacks authority schema')
        base.FUNCTIONS = dict(base.FUNCTIONS, completion_fence_claim_retention=RETENTION_BODY)
    base.verify_fence(bind)


def _install_retention(bind, body):
    bind.execute(sa.text(
        "CREATE OR REPLACE FUNCTION public.completion_fence_claim_retention() "
        "RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER VOLATILE "
        "SET search_path = pg_catalog, pg_temp AS $fence$" + body + "$fence$"))


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
        if not table.foreign_keys:
            # The erasure record deliberately outlives the account: no fence.
            if foreign:
                raise RuntimeError('Health erasure record must not cascade with the account')
        elif not any(f['constrained_columns'] == ['owner_id'] and f['referred_table'] == 'user'
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

    if bind.dialect.name == 'postgresql':
        verify_fence(bind)  # refuse any altered/partial fence before replacing
        _install_retention(bind, RETENTION_BODY)
        verify_fence(bind)


def downgrade():
    connection = op.get_bind()
    tables = ('health_legal_hold', 'health_erasure')
    for table in tables:
        if connection.execute(sa.text('SELECT 1 FROM ' + table + ' LIMIT 1')).first():
            raise RuntimeError('Health consent downgrade refuses to erase consent or erasure evidence; retain schema on rollback')
    if connection.dialect.name == 'postgresql':
        verify_fence(connection)
        base = _base_fence()
        _install_retention(connection, base.FUNCTIONS['completion_fence_claim_retention'])
        base.verify_fence(connection)
    for table in tables:
        op.drop_table(table)
