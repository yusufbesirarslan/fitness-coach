"""Add the bounded Coach-message history access path.

Both production reads fix ``conversation_id`` and request newest message IDs.
PostgreSQL can scan this plain ascending btree backward for ``id DESC``; the
context read can additionally apply its ``id`` lower bound in the same index.

The existing single-column ``ix_coach_message_conversation_id`` remains in
place. Removing an established access path needs separate consumer and rollback
evidence, while this revision is deliberately additive and independently
reversible.

The guards support the normal deployed upgrade and the fresh boot path where
``db.create_all()`` has already built model indexes before Alembic replays.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""
import sqlalchemy as sa
from alembic import op


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


TABLE = "coach_message"
INDEX = "ix_coach_message_conversation_id_id"
COLUMNS = ["conversation_id", "id"]


def _has_index(bind):
    inspector = sa.inspect(bind)
    return any(
        item["name"] == INDEX for item in inspector.get_indexes(TABLE))


def upgrade():
    bind = op.get_bind()
    if _has_index(bind):
        return
    op.create_index(INDEX, TABLE, COLUMNS, unique=False)


def downgrade():
    bind = op.get_bind()
    if not _has_index(bind):
        return
    op.drop_index(INDEX, table_name=TABLE)
