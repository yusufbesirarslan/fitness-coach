"""Allow Cognito users without local password hashes.

Sprint 1 moves registration to Cognito while preserving legacy local auth.
New Cognito-created users must not duplicate passwords locally, so
user.password_hash becomes nullable. Existing legacy users keep their hashes.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-09
"""
from alembic import op


revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE "user" ALTER COLUMN password_hash DROP NOT NULL')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('UPDATE "user" SET password_hash = \'\' WHERE password_hash IS NULL')
    op.execute('ALTER TABLE "user" ALTER COLUMN password_hash SET NOT NULL')
