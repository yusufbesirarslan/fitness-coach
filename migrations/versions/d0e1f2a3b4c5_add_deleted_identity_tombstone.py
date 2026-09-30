"""add the deleted-identity tombstone (LP-11 anti-resurrection record)

Revision ID: d0e1f2a3b4c5
Revises: c8d9e0f1a2b3
Create Date: 2026-09-30

`DELETE /api/v1/account` writes one row here in the same transaction that
purges the account; web and mobile login refuse to give a local row back to a
provider subject that has one (docs/MOBILE_ACCOUNT_DELETION.md §6). A row holds
a keyed HMAC-SHA256 fingerprint of the Cognito subject and the deletion time —
no subject, username, e-mail, token or profile data. It is a security record,
not retained account data, and is kept indefinitely.

The fresh-database boot path may run ``db.create_all()`` before Alembic, so this
revision verifies an existing table rather than creating it twice. It is
additive and reversible: downgrade removes only this table (and with it the
protection it records).
"""

from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c5"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None

_TABLE = "deleted_identity_tombstone"
_REQUIRED_COLUMNS = {"fingerprint", "deleted_at"}


def _create_table():
    op.create_table(
        _TABLE,
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("fingerprint"),
    )


def _verify_existing(bind):
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(_TABLE)}
    if columns != _REQUIRED_COLUMNS:
        raise RuntimeError(
            f"{_TABLE} exists with unexpected columns: {sorted(columns)}")
    primary = inspector.get_pk_constraint(_TABLE).get("constrained_columns")
    if primary != ["fingerprint"]:
        raise RuntimeError(f"{_TABLE} primary key must be (fingerprint)")


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(_TABLE):
        _create_table()
    else:
        _verify_existing(bind)


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
