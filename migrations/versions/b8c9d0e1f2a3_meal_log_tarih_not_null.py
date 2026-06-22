"""MealLog.tarih: NULL'ları backfill et + NOT NULL yap.

tarih kanonik gün anahtarıdır (Istanbul); NULL satırlar per-gün sorgularından
sessizce kaçıyordu. Önce NULL'ları created_at'in Istanbul tarihinden (created_at
da NULL ise bugünden) doldur, sonra NOT NULL kısıtını ekle. Postgres hedefli;
taze DB'ler kısıtı zaten modelden (create_all) alır, SQLite (test) ortamında
no-op. Idempotent: NULL kalmadıysa UPDATE'ler hiçbir satıra dokunmaz.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-22
"""
from alembic import op


revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: NOT NULL + default create_all'dan zaten geliyor; no-op.
        return
    # created_at naive UTC → Istanbul yerel tarihine çevirerek backfill et.
    op.execute(
        "UPDATE meal_log "
        "SET tarih = to_char((created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Istanbul'), 'YYYY-MM-DD') "
        "WHERE tarih IS NULL AND created_at IS NOT NULL"
    )
    # created_at da NULL olan patolojik satırlar için bugüne düş.
    op.execute(
        "UPDATE meal_log "
        "SET tarih = to_char((now() AT TIME ZONE 'Europe/Istanbul'), 'YYYY-MM-DD') "
        "WHERE tarih IS NULL"
    )
    op.execute('ALTER TABLE meal_log ALTER COLUMN tarih SET NOT NULL')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE meal_log ALTER COLUMN tarih DROP NOT NULL')
