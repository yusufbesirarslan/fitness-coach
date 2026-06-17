"""user tablosuna cognito_sub (Amazon Cognito e-posta ile giriş kimlik bağı)

Bu migration YALNIZCA mevcut (alembic_version'lı) Postgres DB'leri içindir; taze
DB'ler şemayı zaten güncel modellerden (db.create_all) alır ve bu revizyon stamp
ile "uygulanmış" işaretlenir. SQLite (test/lokal) ortamında no-op'tur.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16
"""
from alembic import op


revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: taze şema create_all'dan cognito_sub'ı zaten içerir; no-op.
        return
    op.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS cognito_sub VARCHAR(64)')
    op.execute(
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_user_cognito_sub '
        'ON "user" (cognito_sub)'
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('DROP INDEX IF EXISTS uq_user_cognito_sub')
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS cognito_sub')
