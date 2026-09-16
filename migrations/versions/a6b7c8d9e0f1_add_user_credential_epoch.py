"""add the user credential epoch that fences logins across a password change

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-09-16

F1 remediation, second half. A mobile login authenticates against Cognito over
the network and only then writes its ``mobile_auth_session`` row, so a password
reset can land in between: the login used the OLD password but its family is
created after the reset already collected the families to revoke. The epoch is
the fence. ``mobile_auth.login`` reads it before the provider call and re-reads
it under a row lock before inserting the family; ``revoke_all_for_user`` bumps
it under the same lock before it looks for families. Equal epochs mean no
credential change committed in between, which is the only question the login has
to answer.

Purely additive: one integer column with a server default, so existing rows read
0 without a backfill and any row written by the shipped code stays valid. The
fresh-database boot path may run ``db.create_all()`` before Alembic, so this
revision adds the column only if it is actually missing.
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None

_TABLE = "user"
_COLUMN = "credential_epoch"


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        raise RuntimeError(f"{_TABLE} is missing; apply the base revision first")
    if _COLUMN in {item["name"] for item in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(
        _COLUMN, sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return
    if _COLUMN not in {item["name"] for item in inspector.get_columns(_TABLE)}:
        return
    op.drop_column(_TABLE, _COLUMN)
