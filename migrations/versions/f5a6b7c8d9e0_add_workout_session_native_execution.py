"""add native workout-session execution state

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-02

Mobile Training PR5. Purely additive columns on the existing ``workout_session``
table: the native workout reference/lineage captured at start, the optimistic
checkpoint revision, the bounded checkpoint snapshot and its durable replay
identity. No column is dropped, no existing column is altered, and every new
column is nullable or has a server default, so rows written by the shipped PR3
lifecycle stay valid and readable.

The fresh-database boot path may run ``db.create_all()`` before Alembic, so this
revision adds only the columns that are actually missing.
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None

_TABLE = "workout_session"
_COLUMNS = (
    ("workout_ref", sa.Column("workout_ref", sa.String(length=32), nullable=True)),
    ("plan_lineage_id", sa.Column("plan_lineage_id", sa.String(length=64), nullable=True)),
    ("plan_mutation_version", sa.Column("plan_mutation_version", sa.Integer(), nullable=True)),
    ("checkpoint_revision", sa.Column(
        "checkpoint_revision", sa.Integer(), nullable=False, server_default="0")),
    ("checkpoint_data", sa.Column("checkpoint_data", sa.Text(), nullable=True)),
    ("checkpoint_at", sa.Column("checkpoint_at", sa.DateTime(), nullable=True)),
    ("checkpoint_idempotency_key", sa.Column(
        "checkpoint_idempotency_key", sa.String(length=64), nullable=True)),
    ("checkpoint_fingerprint", sa.Column(
        "checkpoint_fingerprint", sa.String(length=64), nullable=True)),
)


def _existing(bind):
    return {item["name"] for item in sa.inspect(bind).get_columns(_TABLE)}


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        raise RuntimeError(
            f"{_TABLE} is missing; apply the Sprint 7 PR3 revision first")
    existing = _existing(bind)
    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, column)


def downgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        return
    existing = _existing(bind)
    for name, _column in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(_TABLE, name)
