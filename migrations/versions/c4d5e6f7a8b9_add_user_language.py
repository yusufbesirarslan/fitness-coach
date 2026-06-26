"""user tablosuna language kolonu (TR/EN dil tercihi)

Bu migration YALNIZCA mevcut (alembic_version'lı) Postgres DB'leri içindir; taze
DB'ler şemayı zaten güncel modellerden (db.create_all) alır ve bu revizyon stamp
ile "uygulanmış" işaretlenir. SQLite (test/lokal) ortamında no-op'tur. Varsayılan
'tr' → mevcut tüm kullanıcılar Türkçe kalır (davranış değişmez).

Revision ID: c4d5e6f7a8b9
Revises: b8c9d0e1f2a3
Create Date: 2026-06-26
"""
from alembic import op


revision = 'c4d5e6f7a8b9'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: taze şema create_all'dan language'ı zaten içerir; no-op.
        return
    op.execute("ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'tr'")
    # Mevcut NULL satırları (kolon eklenmeden önce var olanlar) varsayılana çek.
    op.execute("UPDATE \"user\" SET language = 'tr' WHERE language IS NULL")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS language')
