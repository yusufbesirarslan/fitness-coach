"""Pump Check sharing fields and interactions.

Revision ID: f2a3b4c5d6e7
Revises: ab12cd34ef56
Create Date: 2026-07-02
"""
from alembic import op


revision = "f2a3b4c5d6e7"
down_revision = "ab12cd34ef56"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) NOT NULL DEFAULT 'private'")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS shared_friend_ids JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS likes_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS comments_count INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_visibility ON pump_check (visibility)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS pump_check_like (
            id SERIAL PRIMARY KEY,
            pump_check_id INTEGER NOT NULL REFERENCES pump_check(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITHOUT TIME ZONE,
            CONSTRAINT uq_pump_check_like_user UNIQUE (pump_check_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_like_pump_check_id ON pump_check_like (pump_check_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_like_user_id ON pump_check_like (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_like_created_at ON pump_check_like (created_at)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS pump_check_comment (
            id SERIAL PRIMARY KEY,
            pump_check_id INTEGER NOT NULL REFERENCES pump_check(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            body VARCHAR(500) NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_pump_check_id ON pump_check_comment (pump_check_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_user_id ON pump_check_comment (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pump_check_comment_created_at ON pump_check_comment (created_at)")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_pump_check_like_created_at")
    op.execute("DROP TABLE IF EXISTS pump_check_comment")
    op.execute("DROP TABLE IF EXISTS pump_check_like")
    op.execute("DROP INDEX IF EXISTS ix_pump_check_visibility")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS comments_count")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS likes_count")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS shared_friend_ids")
    op.execute("ALTER TABLE pump_check DROP COLUMN IF EXISTS visibility")
