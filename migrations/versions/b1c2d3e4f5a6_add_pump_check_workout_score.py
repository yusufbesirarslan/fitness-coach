"""Persist workout score on Pump Checks.

Revision ID: b1c2d3e4f5a6
Revises: f2a3b4c5d6e7
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS workout_score DOUBLE PRECISION")
        return
    with op.batch_alter_table("pump_check", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workout_score", sa.Float(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS workout_score")
        return
    with op.batch_alter_table("pump_check", schema=None) as batch_op:
        batch_op.drop_column("workout_score")
