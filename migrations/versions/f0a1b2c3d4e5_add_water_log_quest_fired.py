"""Add per-day water funnel marker (WaterLog.quest_fired).

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""
from alembic import op
import sqlalchemy as sa


revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


_COLUMN = sa.Column(
    "quest_fired", sa.Boolean(), nullable=False, server_default=sa.text("false"))


def _has_column():
    inspector = sa.inspect(op.get_bind())
    return "quest_fired" in {
        column["name"] for column in inspector.get_columns("water_log")}


def upgrade():
    # Taze DB'de create_all kolonu zaten kurar (db_init stamp sonrası bu migration
    # yine koşar) → varlık kontrolü zorunlu.
    if _has_column():
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("water_log") as batch:
            batch.add_column(_COLUMN)
        return
    op.add_column("water_log", _COLUMN)


def downgrade():
    if not _has_column():
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("water_log") as batch:
            batch.drop_column("quest_fired")
        return
    op.drop_column("water_log", "quest_fired")
