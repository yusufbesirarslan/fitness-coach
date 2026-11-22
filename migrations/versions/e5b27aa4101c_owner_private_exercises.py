"""Store immutable owner-private exercises.

Revision ID: e5b27aa4101c
Revises: c1a9b7e5d301
"""

import sqlalchemy as sa
from alembic import op


revision = "e5b27aa4101c"
down_revision = "c1a9b7e5d301"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_exercise",
        sa.Column("exercise_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name_key", sa.String(480), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("equipment", sa.JSON(), nullable=False),
        sa.Column("movement", sa.String(32), nullable=False),
        sa.Column("primary_region", sa.String(32), nullable=False),
        sa.UniqueConstraint("user_id", "name_key", name="uq_private_exercise_owner_name"),
    )
    op.create_index("ix_private_exercise_owner_id", "private_exercise", ["user_id", "exercise_id"])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE private_exercise IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM private_exercise LIMIT 1")).scalar():
        raise RuntimeError("private exercise downgrade would lose durable identities")
    op.drop_index("ix_private_exercise_owner_id", table_name="private_exercise")
    op.drop_table("private_exercise")
