"""Add optional per-user Check-in submission key and replay snapshot.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""
from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(
        "weekly_check_in")}
    if "idempotency_key" in columns:
        # Fresh installations may have run db.create_all() before migrations.
        return
    with op.batch_alter_table("weekly_check_in") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("request_fingerprint", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("response_snapshot", sa.Text(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_weekly_checkin_user_key", ["user_id", "idempotency_key"])


def downgrade():
    with op.batch_alter_table("weekly_check_in") as batch_op:
        batch_op.drop_constraint("uq_weekly_checkin_user_key", type_="unique")
        batch_op.drop_column("response_snapshot")
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("idempotency_key")
