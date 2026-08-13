"""Add canonical mobile and structured-analysis Pump Check fields.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


_ANALYSIS_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
_COLUMNS = (
    sa.Column("captured_at", sa.DateTime(), nullable=True),
    sa.Column("body_region", sa.String(length=20), nullable=True),
    sa.Column("analysis_status", sa.String(length=20), nullable=True),
    sa.Column("analysis", _ANALYSIS_TYPE, nullable=True),
    sa.Column("analysis_version", sa.String(length=40), nullable=True),
    sa.Column("analysis_started_at", sa.DateTime(), nullable=True),
    sa.Column("analysis_attempt", sa.Integer(), nullable=True),
    sa.Column("analysis_failure_kind", sa.String(length=20), nullable=True),
    sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
)
_UNIQUE = "uq_pump_check_user_idempotency"


def _state():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("pump_check")}
    uniques = {item["name"] for item in inspector.get_unique_constraints("pump_check")}
    return columns, uniques


def upgrade():
    columns, uniques = _state()
    missing = [column for column in _COLUMNS if column.name not in columns]
    if op.get_bind().dialect.name == "sqlite":
        if not missing and _UNIQUE in uniques:
            return
        with op.batch_alter_table("pump_check") as batch:
            for column in missing:
                batch.add_column(column)
            if _UNIQUE not in uniques:
                batch.create_unique_constraint(
                    _UNIQUE, ["user_id", "idempotency_key"])
        return
    for column in missing:
        op.add_column("pump_check", column)
    if _UNIQUE not in uniques:
        op.create_unique_constraint(
            _UNIQUE, "pump_check", ["user_id", "idempotency_key"])


def downgrade():
    columns, uniques = _state()
    present = [column.name for column in _COLUMNS if column.name in columns]
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("pump_check") as batch:
            if _UNIQUE in uniques:
                batch.drop_constraint(_UNIQUE, type_="unique")
            for name in reversed(present):
                batch.drop_column(name)
        return
    if _UNIQUE in uniques:
        op.drop_constraint(_UNIQUE, "pump_check", type_="unique")
    for name in reversed(present):
        op.drop_column("pump_check", name)
