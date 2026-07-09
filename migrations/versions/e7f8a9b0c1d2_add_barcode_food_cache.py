"""Add normalized barcode food cache.

Revision ID: e7f8a9b0c1d2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e7f8a9b0c1d2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()
    op.create_table(
        "barcode_food_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=32), nullable=False),
        sa.Column("food_id", sa.String(length=50), nullable=True),
        sa.Column("food_name", sa.String(length=200), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=True),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_barcode_food_cache_barcode", "barcode_food_cache", ["barcode"], unique=True)
    op.create_index("ix_barcode_food_cache_food_id", "barcode_food_cache", ["food_id"], unique=False)


def downgrade():
    op.drop_index("ix_barcode_food_cache_food_id", table_name="barcode_food_cache")
    op.drop_index("ix_barcode_food_cache_barcode", table_name="barcode_food_cache")
    op.drop_table("barcode_food_cache")
