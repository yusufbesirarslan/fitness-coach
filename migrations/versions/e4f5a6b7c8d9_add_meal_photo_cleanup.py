"""add durable meal-photo cleanup intents

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-09-02

Sprint 13 PR4 remediation. Deleting a ledger row commits before its stored
photo is released, and the key carries a random uuid4 that cannot be rebuilt
from the opaque entry token. Without a durable record a failed release would
therefore leave an object nothing could ever name again. This table is written
inside the same transaction as the row delete and removed once the object is
gone, so it is normally empty; a non-empty table means unfinished cleanup work
(``flask --app starter cleanup-pending-meal-photos``).

The fresh-database boot path may run ``db.create_all()`` before Alembic, so this
revision verifies an existing table rather than creating it twice. It is
additive and reversible: downgrade removes only this table.
"""

from alembic import op
import sqlalchemy as sa


revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

_TABLE = "meal_photo_cleanup"
_KEY_UNIQUE = "uq_meal_photo_cleanup_key"
_OWNER_ENTRY = "ix_meal_photo_cleanup_owner_entry"
_REQUIRED_COLUMNS = {
    "id", "user_id", "entry_id", "photo_key", "entry_revision", "diary_date",
    "created_at",
}


def _create_table():
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # Deliberately NOT a foreign key: the meal_log row it names is deleted
        # in the very transaction that writes this record.
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("photo_key", sa.String(length=300), nullable=False),
        sa.Column("entry_revision", sa.String(length=64), nullable=False),
        sa.Column("diary_date", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # The deleted RESOURCE is the object, not the entry id: SQLite may reuse
        # a rowid once the highest row is gone, so uniqueness on (user_id,
        # entry_id) could later reject a legitimate second intent. Meal photo
        # keys carry a uuid4 and are never shared by two rows.
        sa.UniqueConstraint("photo_key", name=_KEY_UNIQUE),
    )


def _index_names(bind):
    return {
        item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)
        if item.get("name")
    }


def _ensure_indexes(bind):
    if _OWNER_ENTRY not in _index_names(bind):
        op.create_index(
            _OWNER_ENTRY, _TABLE, ["user_id", "entry_id"], unique=False)


def _verify_existing(bind):
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    missing = _REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            f"{_TABLE} exists but is missing required columns: {sorted(missing)}")
    unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(_TABLE)
    }
    unique_columns.update(
        tuple(item["column_names"])
        for item in inspector.get_indexes(_TABLE) if item.get("unique")
    )
    if ("photo_key",) not in unique_columns:
        raise RuntimeError(f"{_TABLE} is missing {_KEY_UNIQUE}")


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        _create_table()
    else:
        _verify_existing(bind)
    _ensure_indexes(bind)


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
