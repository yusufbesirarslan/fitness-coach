"""nutrition plan versioning + edit journal (Adaptive Coach C1)

Revision ID: c1a0d2e3f4a5
Revises: ad17de1e0001
Create Date: 2026-11-18

The Nutrition Plan twin of b3c4d5e6f7a8, as a strict EXPAND step: two additive
columns on ``nutrition_plan`` and one new table. The previous revision keeps
writing plan rows without a lineage during a dual-revision release, so
``lineage_id`` stays NULLABLE and is not backfilled: a row gets its lineage
under the plan lock on its first targeted edit (``nutrition_plan_mutation``),
and a NULL lineage simply means "never edited, no history". Every existing
row is version 0 with an empty journal — no history is invented.

Verify-or-create, because the fresh-DB boot path runs ``db.create_all()``
before upgrading.
"""
from alembic import op
import sqlalchemy as sa


revision = "c1a0d2e3f4a5"
down_revision = "ad17de1e0001"
branch_labels = None
depends_on = None

expand_contract = "expand"

_PLAN = "nutrition_plan"
_JOURNAL = "nutrition_plan_mutation_record"
_JOURNAL_INDEXES = (
    "ix_nutrition_plan_mutation_record_user_id", "ix_nutrition_mutation_lineage")
_REQUIRED_JOURNAL_COLUMNS = {
    "id", "public_id", "user_id", "plan_lineage_id", "operation_kind",
    "command_type", "command_fingerprint", "actor", "reason", "outcome",
    "before_version", "after_version", "before_snapshot", "after_snapshot",
    "before_fingerprint", "after_fingerprint", "provenance", "idempotency_key",
    "reverts_mutation_id", "created_at",
}


def _index_names(insp, table):
    return {ix["name"] for ix in insp.get_indexes(table) if ix.get("name")}


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {col["name"] for col in insp.get_columns(_PLAN)}
    if "lineage_id" not in columns:
        op.add_column(_PLAN, sa.Column("lineage_id", sa.String(length=64), nullable=True))
    if "mutation_version" not in columns:
        op.add_column(_PLAN, sa.Column(
            "mutation_version", sa.Integer(), nullable=False, server_default="0"))

    insp = sa.inspect(bind)
    if not insp.has_table(_JOURNAL):
        op.create_table(
            _JOURNAL,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("public_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("plan_lineage_id", sa.String(length=64), nullable=False),
            sa.Column("operation_kind", sa.String(length=20), nullable=False),
            sa.Column("command_type", sa.String(length=60), nullable=False),
            sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("actor", sa.String(length=20), nullable=False),
            sa.Column("reason", sa.String(length=200), nullable=True),
            sa.Column("outcome", sa.String(length=20), nullable=False),
            sa.Column("before_version", sa.Integer(), nullable=False),
            sa.Column("after_version", sa.Integer(), nullable=False),
            sa.Column("before_snapshot", sa.Text(), nullable=False),
            sa.Column("after_snapshot", sa.Text(), nullable=False),
            sa.Column("before_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("after_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("provenance", sa.Text(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("reverts_mutation_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("public_id", name="uq_nutrition_mutation_public_id"),
            sa.UniqueConstraint(
                "user_id", "idempotency_key", name="uq_nutrition_mutation_user_key"),
            sa.UniqueConstraint(
                "reverts_mutation_id", name="uq_nutrition_mutation_reverts"),
        )
        existing = frozenset()
    else:
        missing = _REQUIRED_JOURNAL_COLUMNS - {
            c["name"] for c in insp.get_columns(_JOURNAL)}
        if missing:
            raise RuntimeError(
                "%s exists but is missing required column(s) %s; reconcile it "
                "with app/models.py NutritionPlanMutationRecord."
                % (_JOURNAL, sorted(missing)))
        existing = _index_names(insp, _JOURNAL)

    if "ix_nutrition_plan_mutation_record_user_id" not in existing:
        op.create_index("ix_nutrition_plan_mutation_record_user_id", _JOURNAL,
                        ["user_id"], unique=False)
    if "ix_nutrition_mutation_lineage" not in existing:
        op.create_index("ix_nutrition_mutation_lineage", _JOURNAL,
                        ["user_id", "plan_lineage_id", "id"], unique=False)


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table(_JOURNAL):
        # Edit history is the only record of what an edit or undo changed;
        # a schema rollback must never erase it silently. Code rollback keeps
        # the table (expand-only); only an empty ledger may be dropped.
        if bind.execute(sa.text(f"SELECT 1 FROM {_JOURNAL} LIMIT 1")).first() is not None:
            raise RuntimeError(
                "refusing to downgrade c1a0d2e3f4a5: nutrition plan edit history "
                "exists; roll back the code only (the schema is additive)")
        existing = _index_names(insp, _JOURNAL)
        for name in _JOURNAL_INDEXES:
            if name in existing:
                op.drop_index(name, table_name=_JOURNAL)
        op.drop_table(_JOURNAL)
    columns = {c["name"] for c in sa.inspect(bind).get_columns(_PLAN)}
    if not (columns & {"lineage_id", "mutation_version"}):
        return
    with op.batch_alter_table(_PLAN) as batch_op:
        if "mutation_version" in columns:
            batch_op.drop_column("mutation_version")
        if "lineage_id" in columns:
            batch_op.drop_column("lineage_id")
