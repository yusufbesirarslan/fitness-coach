"""user tablosuna user_metadata (AI koç hafızası: sakatlıklar, tercihler — JSONB)

Bu migration YALNIZCA mevcut (alembic_version'lı) Postgres DB'leri içindir; taze
DB'ler şemayı zaten güncel modellerden (db.create_all) alır ve bu revizyon stamp
ile "uygulanmış" işaretlenir. SQLite (test/lokal) ortamında no-op'tur.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-19
"""
from alembic import op


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: taze şema create_all'dan user_metadata'yı zaten içerir; no-op.
        return
    op.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS user_metadata JSONB')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS user_metadata')
