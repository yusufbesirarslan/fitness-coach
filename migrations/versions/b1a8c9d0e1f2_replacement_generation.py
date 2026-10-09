"""Expand-only replacement generation operation authority."""
from alembic import op
import sqlalchemy as sa
revision = 'b1a8c9d0e1f2'
down_revision = 'e3f4a5b6c7d8'
branch_labels = None
depends_on = None
expand_contract = 'expand'
TABLE = 'training_plan_replacement_generation_operation'


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(TABLE):
        _verify_existing(bind, TABLE)
    else:
        op.create_table(TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('key_digest', sa.String(64), nullable=False),
        sa.Column('intent_fingerprint', sa.String(64), nullable=False),
        sa.Column('base_lineage_id', sa.String(64), nullable=False),
        sa.Column('base_mutation_version', sa.Integer(), nullable=False),
        sa.Column('base_snapshot_digest', sa.String(64), nullable=False),
        sa.Column('generation_context', sa.Text(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('lease_token', sa.String(64), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
        sa.Column('proposal_public_id', sa.String(64), nullable=True),
        sa.Column('proposal_expires_at', sa.DateTime(), nullable=True),
        sa.Column('review_data', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(64), nullable=True),
        sa.Column('error_http_status', sa.Integer(), nullable=True),
        sa.Column('quota_reserved', sa.Boolean(), nullable=False),
        sa.Column('quota_week', sa.String(10), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'key_digest', name='uq_replacement_generation_owner_key'),
        sa.CheckConstraint('attempt_count >= 1 AND attempt_count <= 2', name='ck_replacement_generation_attempts'),
        sa.CheckConstraint('generation_context IS NULL OR length(generation_context) <= 32768', name='ck_replacement_generation_context'),
        sa.CheckConstraint("(status = 'IN_PROGRESS' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND generation_context IS NOT NULL AND completed_at IS NULL AND proposal_public_id IS NULL AND proposal_expires_at IS NULL AND review_data IS NULL AND error_code IS NULL AND error_http_status IS NULL) OR (status = 'SUCCEEDED' AND lease_token IS NULL AND lease_expires_at IS NULL AND generation_context IS NULL AND completed_at IS NOT NULL AND proposal_public_id IS NOT NULL AND proposal_expires_at IS NOT NULL AND review_data IS NOT NULL AND error_code IS NULL AND error_http_status IS NULL) OR (status = 'FAILED' AND lease_token IS NULL AND lease_expires_at IS NULL AND generation_context IS NULL AND completed_at IS NOT NULL AND proposal_public_id IS NULL AND proposal_expires_at IS NULL AND review_data IS NULL AND error_code IS NOT NULL AND error_http_status IS NOT NULL)", name='ck_replacement_generation_result'),
        sa.CheckConstraint("status IN ('IN_PROGRESS', 'SUCCEEDED', 'FAILED')", name='ck_replacement_generation_status'),
        sa.CheckConstraint('review_data IS NULL OR length(review_data) <= 524288', name='ck_replacement_generation_review'),
        sa.CheckConstraint('base_mutation_version >= 0', name='ck_replacement_generation_version'),
        sa.Index('uq_replacement_generation_active_owner', 'user_id', unique=True,
                 postgresql_where=sa.text("status = 'IN_PROGRESS'"), sqlite_where=sa.text("status = 'IN_PROGRESS'")))


SCHEMA = {'training_plan_replacement_generation_operation': {'checks': {'ck_replacement_generation_attempts': 'attempt_count '
                                                                                                     '>= '
                                                                                                     '1 '
                                                                                                     'AND '
                                                                                                     'attempt_count '
                                                                                                     '<= '
                                                                                                     '2',
                                                               'ck_replacement_generation_context': 'generation_context '
                                                                                                    'IS '
                                                                                                    'NULL '
                                                                                                    'OR '
                                                                                                    'length(generation_context) '
                                                                                                    '<= '
                                                                                                    '32768',
                                                               'ck_replacement_generation_result': '(status '
                                                                                                   '= '
                                                                                                   "'IN_PROGRESS' "
                                                                                                   'AND '
                                                                                                   'lease_token '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'lease_expires_at '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'generation_context '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'completed_at '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_public_id '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_expires_at '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'review_data '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_code '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_http_status '
                                                                                                   'IS '
                                                                                                   'NULL) '
                                                                                                   'OR '
                                                                                                   '(status '
                                                                                                   '= '
                                                                                                   "'SUCCEEDED' "
                                                                                                   'AND '
                                                                                                   'lease_token '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'lease_expires_at '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'generation_context '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'completed_at '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_public_id '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_expires_at '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'review_data '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_code '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_http_status '
                                                                                                   'IS '
                                                                                                   'NULL) '
                                                                                                   'OR '
                                                                                                   '(status '
                                                                                                   '= '
                                                                                                   "'FAILED' "
                                                                                                   'AND '
                                                                                                   'lease_token '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'lease_expires_at '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'generation_context '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'completed_at '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_public_id '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'proposal_expires_at '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'review_data '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_code '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL '
                                                                                                   'AND '
                                                                                                   'error_http_status '
                                                                                                   'IS '
                                                                                                   'NOT '
                                                                                                   'NULL)',
                                                               'ck_replacement_generation_review': 'review_data '
                                                                                                   'IS '
                                                                                                   'NULL '
                                                                                                   'OR '
                                                                                                   'length(review_data) '
                                                                                                   '<= '
                                                                                                   '524288',
                                                               'ck_replacement_generation_status': 'status '
                                                                                                   'IN '
                                                                                                   "('IN_PROGRESS', "
                                                                                                   "'SUCCEEDED', "
                                                                                                   "'FAILED')",
                                                               'ck_replacement_generation_version': 'base_mutation_version '
                                                                                                    '>= '
                                                                                                    '0'},
                                                    'columns': {'attempt_count': ('INTEGER', False),
                                                                'base_lineage_id': ('VARCHAR(64)',
                                                                                    False),
                                                                'base_mutation_version': ('INTEGER',
                                                                                          False),
                                                                'base_snapshot_digest': ('VARCHAR(64)',
                                                                                         False),
                                                                'completed_at': ('DATETIME', True),
                                                                'created_at': ('DATETIME', False),
                                                                'error_code': ('VARCHAR(64)', True),
                                                                'error_http_status': ('INTEGER',
                                                                                      True),
                                                                'generation_context': ('TEXT',
                                                                                       True),
                                                                'id': ('INTEGER', False),
                                                                'intent_fingerprint': ('VARCHAR(64)',
                                                                                       False),
                                                                'key_digest': ('VARCHAR(64)',
                                                                               False),
                                                                'lease_expires_at': ('DATETIME',
                                                                                     True),
                                                                'lease_token': ('VARCHAR(64)',
                                                                                True),
                                                                'proposal_expires_at': ('DATETIME',
                                                                                        True),
                                                                'proposal_public_id': ('VARCHAR(64)',
                                                                                       True),
                                                                'quota_reserved': ('BOOLEAN',
                                                                                   False),
                                                                'quota_week': ('VARCHAR(10)', True),
                                                                'review_data': ('TEXT', True),
                                                                'status': ('VARCHAR(16)', False),
                                                                'updated_at': ('DATETIME', False),
                                                                'user_id': ('INTEGER', False)},
                                                    'indexes': {'uq_replacement_generation_active_owner': (('user_id',),
                                                                                                           1,
                                                                                                           'status '
                                                                                                           '= '
                                                                                                           "'IN_PROGRESS'")},
                                                    'unique': {'uq_replacement_generation_owner_key': ('user_id',
                                                                                                       'key_digest')}}}

def _normalize(sql):
    import re
    sql = re.sub(r'::(?:text|character varying|integer|boolean)(?:\[\])?', '', sql.lower())
    sql = sql.replace('= any', 'in').replace('array[', '').replace(']', '')
    return re.sub(r"[\s()\"]", '', sql)


def _verify_existing(bind, name):
    inspector = sa.inspect(bind)
    spec = SCHEMA[name]
    actual = {c["name"]: (("DATETIME" if isinstance(c["type"], sa.DateTime) else "FLOAT" if isinstance(c["type"], sa.Float) else str(c["type"])), c["nullable"]) for c in inspector.get_columns(name)}
    if actual != spec["columns"]:
        raise RuntimeError("generation schema columns mismatch")
    if inspector.get_pk_constraint(name).get("constrained_columns") != ["id"]:
        raise RuntimeError("generation schema primary key missing")
    unique = {c["name"]: tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
    if unique != spec["unique"]:
        raise RuntimeError("generation schema uniqueness missing")
    checks = {c["name"]: _normalize(c["sqltext"]) for c in inspector.get_check_constraints(name)}
    if checks != {key: _normalize(value) for key, value in spec["checks"].items()}:
        raise RuntimeError("generation schema checks missing")
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(name)
               if not i.get("duplicates_constraint")}
    if indexes != {key: value[:2] for key, value in spec["indexes"].items()}:
        raise RuntimeError("generation schema indexes missing")
    for i in inspector.get_indexes(name):
        if i["name"] not in spec["indexes"]:
            continue
        expected = spec["indexes"][i["name"]][2]
        predicate = str(i.get("dialect_options", {}).get(bind.dialect.name + "_where", ""))
        if expected and expected.replace(" ", "").replace("(", "").replace(")", "") != predicate.replace(" ", "").replace("(", "").replace(")", "").replace("::text", ""):
            raise RuntimeError("generation schema index predicate missing")
    fk = inspector.get_foreign_keys(name)
    if len(fk) != 1 or fk[0]["constrained_columns"] != ["user_id"] or fk[0]["referred_table"] != "user" or fk[0]["referred_columns"] != ["id"] or fk[0].get("options", {}).get("ondelete") != "CASCADE":
        raise RuntimeError("generation schema owner cascade missing")



def downgrade():
    raise RuntimeError('expand-only generation authority; retain replay evidence')
