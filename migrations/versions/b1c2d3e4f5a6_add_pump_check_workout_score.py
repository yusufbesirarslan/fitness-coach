"""Persist workout score on Pump Checks.

Revision ID: b1c2d3e4f5a6
Revises: f2a3b4c5d6e7
Create Date: 2026-07-03
"""
from alembic import op


revision = "b1c2d3e4f5a6"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS workout_score DOUBLE PRECISION")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS workout_score")
