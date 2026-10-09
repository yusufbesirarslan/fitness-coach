"""LP18-B0 additive proposal/receipt authority; no existing-table writes."""
from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None
expand_contract = "expand"


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("training_plan_replacement_proposal"):
        _verify_existing(bind, "training_plan_replacement_proposal")
    else:
        op.create_table('training_plan_replacement_proposal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('public_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('base_lineage_id', sa.String(length=64), nullable=False),
        sa.Column('base_mutation_version', sa.Integer(), nullable=False),
        sa.Column('base_snapshot_digest', sa.String(length=64), nullable=False),
        sa.Column('candidate_plan_data', sa.Text(), nullable=False),
        sa.Column('candidate_score', sa.Float(), nullable=False),
        sa.Column('candidate_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint('base_mutation_version >= 0', name='ck_replacement_proposal_version'),
        sa.CheckConstraint('expires_at > created_at', name='ck_replacement_proposal_expiry'),
        sa.CheckConstraint('length(candidate_plan_data) <= 262144', name='ck_replacement_candidate_size'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('public_id', name='uq_replacement_proposal_public')
        )
        op.create_index('ix_replacement_proposal_owner_expiry', 'training_plan_replacement_proposal', ['user_id', 'expires_at'], unique=False)
    if sa.inspect(bind).has_table("training_plan_replacement_receipt"):
        _verify_existing(bind, "training_plan_replacement_receipt")
    else:
        op.create_table('training_plan_replacement_receipt',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('public_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('key_digest', sa.String(length=64), nullable=False),
        sa.Column('intent_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('proposal_public_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('result_lineage_id', sa.String(length=64), nullable=True),
        sa.Column('result_mutation_version', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("(status = 'APPLIED' AND result_lineage_id IS NOT NULL AND result_mutation_version IS NOT NULL AND result_mutation_version = 0) OR (status <> 'APPLIED' AND result_lineage_id IS NULL AND result_mutation_version IS NULL)", name='ck_replacement_receipt_result'),
        sa.CheckConstraint("status IN ('APPLIED', 'STALE', 'EXPIRED', 'ACTIVE_REFUSED', 'COACH_PENDING_REFUSED', 'CONSUMED')", name='ck_replacement_receipt_status'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('public_id', name='uq_replacement_receipt_public'),
        sa.UniqueConstraint('user_id', 'key_digest', name='uq_replacement_receipt_owner_key'),
        # This uniqueness belongs to the NEW table; the serving revision never
        # writes it. Define it with the table, not on an existing writer table.
        sa.Index('uq_replacement_receipt_applied_proposal', 'user_id', 'proposal_public_id',
                 unique=True, sqlite_where=sa.text("status = 'APPLIED'"),
                 postgresql_where=sa.text("status = 'APPLIED'"))
        )
        op.create_index('ix_replacement_receipt_retention', 'training_plan_replacement_receipt', ['created_at'], unique=False)


SCHEMA = {'training_plan_replacement_proposal': {'columns': {'id': ('INTEGER', False),
                                                    'public_id': ('VARCHAR(64)', False),
                                                    'user_id': ('INTEGER', False),
                                                    'base_lineage_id': ('VARCHAR(64)', False),
                                                    'base_mutation_version': ('INTEGER', False),
                                                    'base_snapshot_digest': ('VARCHAR(64)', False),
                                                    'candidate_plan_data': ('TEXT', False),
                                                    'candidate_score': ('FLOAT', False),
                                                    'candidate_fingerprint': ('VARCHAR(64)', False),
                                                    'created_at': ('DATETIME', False),
                                                    'expires_at': ('DATETIME', False)},
                                        'unique': {'uq_replacement_proposal_public': ('public_id',)},
                                        'checks': {'ck_replacement_proposal_version': 'base_mutation_version '
                                                                                      '>= 0',
                                                   'ck_replacement_candidate_size': 'length(candidate_plan_data) '
                                                                                    '<= 262144',
                                                   'ck_replacement_proposal_expiry': 'expires_at > '
                                                                                     'created_at'},
                                        'indexes': {'ix_replacement_proposal_owner_expiry': (('user_id',
                                                                                              'expires_at'),
                                                                                             False,
                                                                                             '')}},
 'training_plan_replacement_receipt': {'columns': {'id': ('INTEGER', False),
                                                   'public_id': ('VARCHAR(64)', False),
                                                   'user_id': ('INTEGER', False),
                                                   'key_digest': ('VARCHAR(64)', False),
                                                   'intent_fingerprint': ('VARCHAR(64)', False),
                                                   'proposal_public_id': ('VARCHAR(64)', False),
                                                   'status': ('VARCHAR(24)', False),
                                                   'result_lineage_id': ('VARCHAR(64)', True),
                                                   'result_mutation_version': ('INTEGER', True),
                                                   'created_at': ('DATETIME', False)},
                                       'unique': {'uq_replacement_receipt_owner_key': ('user_id',
                                                                                       'key_digest'),
                                                  'uq_replacement_receipt_public': ('public_id',)},
                                       'checks': {'ck_replacement_receipt_result': '(status = '
                                                                                   "'APPLIED' AND "
                                                                                   'result_lineage_id '
                                                                                   'IS NOT NULL '
                                                                                   'AND '
                                                                                   'result_mutation_version '
                                                                                   'IS NOT NULL '
                                                                                   'AND '
                                                                                   'result_mutation_version '
                                                                                   '= 0) OR '
                                                                                   '(status <> '
                                                                                   "'APPLIED' AND "
                                                                                   'result_lineage_id '
                                                                                   'IS NULL AND '
                                                                                   'result_mutation_version '
                                                                                   'IS NULL)',
                                                  'ck_replacement_receipt_status': 'status IN '
                                                                                   "('APPLIED', "
                                                                                   "'STALE', "
                                                                                   "'EXPIRED', "
                                                                                   "'ACTIVE_REFUSED', "
                                                                                   "'COACH_PENDING_REFUSED', "
                                                                                   "'CONSUMED')"},
                                       'indexes': {'ix_replacement_receipt_retention': (('created_at',),
                                                                                        False,
                                                                                        ''),
                                                   'uq_replacement_receipt_applied_proposal': (('user_id',
                                                                                                'proposal_public_id'),
                                                                                               True,
                                                                                               'status '
                                                                                               '= '
                                                                                               "'APPLIED'")}}}

def _verify_existing(bind, name):
    inspector = sa.inspect(bind)
    spec = SCHEMA[name]
    actual = {c["name"]: (("DATETIME" if isinstance(c["type"], sa.DateTime) else "FLOAT" if isinstance(c["type"], sa.Float) else str(c["type"])), c["nullable"]) for c in inspector.get_columns(name)}
    if actual != spec["columns"]:
        raise RuntimeError("replacement schema columns mismatch")
    if inspector.get_pk_constraint(name).get("constrained_columns") != ["id"]:
        raise RuntimeError("replacement schema primary key missing")
    unique = {c["name"]: tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)}
    if unique != spec["unique"]:
        raise RuntimeError("replacement schema uniqueness missing")
    checks = {c["name"] for c in inspector.get_check_constraints(name)}
    if checks != set(spec["checks"]):
        raise RuntimeError("replacement schema checks missing")
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"])) for i in inspector.get_indexes(name)
               if not i.get("duplicates_constraint")}
    if indexes != {key: value[:2] for key, value in spec["indexes"].items()}:
        raise RuntimeError("replacement schema indexes missing")
    for i in inspector.get_indexes(name):
        if i["name"] not in spec["indexes"]:
            continue
        expected = spec["indexes"][i["name"]][2]
        predicate = str(i.get("dialect_options", {}).get(bind.dialect.name + "_where", ""))
        if expected and expected.replace(" ", "").replace("(", "").replace(")", "") != predicate.replace(" ", "").replace("(", "").replace(")", "").replace("::text", ""):
            raise RuntimeError("replacement schema index predicate missing")
    fk = inspector.get_foreign_keys(name)
    if len(fk) != 1 or fk[0]["constrained_columns"] != ["user_id"] or fk[0]["referred_table"] != "user" or fk[0]["referred_columns"] != ["id"] or fk[0].get("options", {}).get("ondelete") != "CASCADE":
        raise RuntimeError("replacement schema owner cascade missing")


def downgrade():
    raise RuntimeError("expand-only replacement authority; retain durable receipts")
