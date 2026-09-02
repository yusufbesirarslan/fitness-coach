"""add durable native training-plan generation operations

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-01

The fresh-database boot path may run ``db.create_all()`` before Alembic. This
revision therefore verifies an existing table rather than creating it twice.
It is additive: downgrade removes only the operation ledger.
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

_TABLE = "training_plan_generation_operation"
_OWNER_KEY = "uq_training_plan_generation_user_key"
_OWNER_STATUS = "ix_training_plan_generation_owner_status"
_ACTIVE_OWNER = "uq_training_plan_generation_active_owner"
_ACTIVE_WHERE = sa.text("status IN ('IN_PROGRESS', 'GENERATED')")
_REQUIRED_COLUMNS = {
    "id", "user_id", "idempotency_key", "request_fingerprint", "status",
    "attempt_count", "candidate_plan_data", "candidate_score",
    "training_plan_id", "plan_lineage_id", "quota_reserved", "quota_week",
    "error_code", "error_http_status", "error_retryable", "created_at",
    "updated_at", "completed_at",
}


def _create_table():
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("candidate_plan_data", sa.Text(), nullable=True),
        sa.Column("candidate_score", sa.Float(), nullable=True),
        sa.Column("training_plan_id", sa.Integer(), nullable=True),
        sa.Column("plan_lineage_id", sa.String(length=64), nullable=True),
        sa.Column("quota_reserved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("quota_week", sa.String(length=8), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_http_status", sa.Integer(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name=_OWNER_KEY),
    )


def _index_names(bind):
    return {
        item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)
        if item.get("name")
    }


def _ensure_indexes(bind):
    existing = _index_names(bind)
    if _OWNER_STATUS not in existing:
        op.create_index(
            _OWNER_STATUS, _TABLE, ["user_id", "status"], unique=False)
    if _ACTIVE_OWNER not in existing:
        op.create_index(
            _ACTIVE_OWNER, _TABLE, ["user_id"], unique=True,
            sqlite_where=_ACTIVE_WHERE, postgresql_where=_ACTIVE_WHERE)


def _verify_existing(bind):
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    missing = _REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            f"{_TABLE} exists but is missing required columns: {sorted(missing)}")
    owner_keys = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(_TABLE)
    }
    if ("user_id", "idempotency_key") not in owner_keys:
        raise RuntimeError(f"{_TABLE} is missing {_OWNER_KEY}")


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        _create_table()
    else:
        _verify_existing(bind)
    _ensure_indexes(bind)


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
