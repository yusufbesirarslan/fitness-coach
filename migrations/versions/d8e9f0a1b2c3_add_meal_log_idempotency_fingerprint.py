"""Add semantic fingerprint storage for mobile meal-log idempotency.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""
from alembic import op
import sqlalchemy as sa


revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def _has_fingerprint_column():
    return any(
        column["name"] == "idempotency_fingerprint"
        for column in sa.inspect(op.get_bind()).get_columns("meal_log")
    )


def upgrade():
    # Fresh local/test databases are created from model metadata before the
    # repository's bootstrap stamps and runs the Alembic chain.
    if _has_fingerprint_column():
        return
    op.add_column(
        "meal_log",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_column("meal_log", "idempotency_fingerprint")
