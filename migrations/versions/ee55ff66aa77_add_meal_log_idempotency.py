"""Add optional replay keys for canonical meal ledger writes.

Revision ID: ee55ff66aa77
Revises: dd44ee55ff66
"""
from alembic import op
import sqlalchemy as sa


revision = "ee55ff66aa77"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None



def _has_column(table, column):
    inspector = sa.inspect(op.get_bind())
    return any(item["name"] == column for item in inspector.get_columns(table))

def upgrade():
    if _has_column("meal_log", "idempotency_key"):
        return
    with op.batch_alter_table("meal_log") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_meal_log_user_idempotency", ["user_id", "idempotency_key"])


def downgrade():
    with op.batch_alter_table("meal_log") as batch_op:
        batch_op.drop_constraint("uq_meal_log_user_idempotency", type_="unique")
        batch_op.drop_column("idempotency_key")
