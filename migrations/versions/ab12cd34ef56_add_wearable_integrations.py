"""Add provider-neutral wearable integration tables.

Revision ID: ab12cd34ef56
Revises: f1a2b3c4d5e6
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa


revision = "ab12cd34ef56"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_wearable_connection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("external_user_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="connected", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", name="uq_wearable_connection_user_provider"),
    )
    with op.batch_alter_table("user_wearable_connection", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_wearable_connection_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_wearable_connection_provider", ["provider"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_wearable_connection_token_expiry"), ["token_expiry"], unique=False)

    op.create_table(
        "wearable_sleep_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("date_key", sa.String(length=10), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("duration_min", sa.Float(), nullable=True),
        sa.Column("deep_sleep_min", sa.Float(), nullable=True),
        sa.Column("rem_sleep_min", sa.Float(), nullable=True),
        sa.Column("light_sleep_min", sa.Float(), nullable=True),
        sa.Column("awake_min", sa.Float(), nullable=True),
        sa.Column("sleep_efficiency", sa.Float(), nullable=True),
        sa.Column("sleep_score", sa.Float(), nullable=True),
        sa.Column("resting_heart_rate", sa.Float(), nullable=True),
        sa.Column("hrv_rmssd", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "source_id", name="uq_wearable_sleep_source"),
    )
    with op.batch_alter_table("wearable_sleep_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wearable_sleep_log_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wearable_sleep_log_date_key"), ["date_key"], unique=False)
        batch_op.create_index("ix_wearable_sleep_user_day", ["user_id", "date_key"], unique=False)

    op.create_table(
        "wearable_activity_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("date_key", sa.String(length=10), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("active_minutes", sa.Float(), nullable=False),
        sa.Column("calories_burned", sa.Float(), nullable=False),
        sa.Column("resting_heart_rate", sa.Float(), nullable=True),
        sa.Column("hrv_rmssd", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "date_key", name="uq_wearable_activity_day"),
    )
    with op.batch_alter_table("wearable_activity_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wearable_activity_log_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wearable_activity_log_date_key"), ["date_key"], unique=False)
        batch_op.create_index(batch_op.f("ix_wearable_activity_log_updated_at"), ["updated_at"], unique=False)
        batch_op.create_index("ix_wearable_activity_user_day", ["user_id", "date_key"], unique=False)

    op.create_table(
        "wearable_workout_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column("date_key", sa.String(length=10), nullable=False),
        sa.Column("sport_name", sa.String(length=120), nullable=True),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("calories_burned", sa.Float(), nullable=True),
        sa.Column("strain", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Float(), nullable=True),
        sa.Column("max_heart_rate", sa.Float(), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "source_id", name="uq_wearable_workout_source"),
    )
    with op.batch_alter_table("wearable_workout_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_wearable_workout_log_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_wearable_workout_log_date_key"), ["date_key"], unique=False)
        batch_op.create_index("ix_wearable_workout_user_day", ["user_id", "date_key"], unique=False)


def downgrade():
    op.drop_table("wearable_workout_log")
    op.drop_table("wearable_activity_log")
    op.drop_table("wearable_sleep_log")
    op.drop_table("user_wearable_connection")
