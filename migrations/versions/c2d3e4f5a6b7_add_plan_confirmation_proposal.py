"""add durable training-plan confirmation proposals (Adaptive Coaching S1 PR4)

Revision ID: c2d3e4f5a6b7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-19

Additive only: one new table, ``training_plan_confirmation_proposal``. Nothing
is dropped, renamed, or rewritten. ``plan_mutation_record`` is untouched — a
pending proposal is not a mutation.

FAIL-SAFE, VERIFY-OR-CREATE: fresh-DB boot runs ``db.create_all()`` before
stamping, so this revision often runs against a table the model already built.
It creates only what is missing.

Revision ID: c2d3e4f5a6b7
Revises: c1d2e3f4a5b6
"""
import sqlalchemy as sa
from alembic import op


revision = "c2d3e4f5a6b7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

_TABLE = "training_plan_confirmation_proposal"
_UQ_PUBLIC = "uq_plan_confirmation_public_id"
_UQ_PENDING = "uq_plan_confirmation_one_pending_per_user"
_CK_STATUS = "ck_plan_confirmation_status"
_IX_USER = "ix_training_plan_confirmation_proposal_user_id"

_REQUIRED_COLUMNS = {
    "id", "public_id", "user_id", "plan_lineage_id", "base_mutation_version",
    "base_snapshot_fingerprint", "command_type", "command_payload",
    "command_fingerprint", "reason_codes", "summary", "status", "created_at",
    "resolved_at",
}


def _columns(insp, table):
    if table not in insp.get_table_names():
        return set()
    return {col["name"] for col in insp.get_columns(table)}


def _index_names(insp, table):
    if table not in insp.get_table_names():
        return set()
    names = {ix["name"] for ix in insp.get_indexes(table) if ix.get("name")}
    try:
        names.update(
            uc["name"] for uc in insp.get_unique_constraints(table)
            if uc.get("name"))
    except Exception:  # noqa: BLE001
        pass
    return names


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = _columns(insp, _TABLE)
    if existing and not _REQUIRED_COLUMNS.issubset(existing):
        missing = sorted(_REQUIRED_COLUMNS - existing)
        raise RuntimeError(
            f"{_TABLE} exists but is missing columns: {missing}")

    if not existing:
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("plan_lineage_id", sa.String(length=64), nullable=False),
            sa.Column("base_mutation_version", sa.Integer(), nullable=False),
            sa.Column("base_snapshot_fingerprint", sa.String(length=64),
                      nullable=False),
            sa.Column("command_type", sa.String(length=60), nullable=False),
            sa.Column("command_payload", sa.JSON(), nullable=False),
            sa.Column("command_fingerprint", sa.String(length=64),
                      nullable=False),
            sa.Column("reason_codes", sa.JSON(), nullable=False),
            sa.Column("summary", sa.String(length=240), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("public_id", name=_UQ_PUBLIC),
            sa.CheckConstraint(
                "status IN ('pending', 'applied', 'cancelled', 'stale', "
                "'superseded')",
                name=_CK_STATUS,
            ),
        )
        _ensure_indexes(frozenset({_UQ_PUBLIC}))
        return

    _ensure_indexes(_index_names(insp, _TABLE))


def _ensure_indexes(existing):
    if _IX_USER not in existing:
        op.create_index(_IX_USER, _TABLE, ["user_id"])
    if _UQ_PENDING not in existing:
        op.create_index(
            _UQ_PENDING, _TABLE, ["user_id"], unique=True,
            sqlite_where=sa.text("status = 'pending'"),
            postgresql_where=sa.text("status = 'pending'"),
        )


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return
    op.drop_table(_TABLE)
