"""PumpCheck günlük idempotency: date_key kolonu + (user_id, date_key) UNIQUE.

Eşzamanlı 'antrenmanı tamamla' isteklerinin TOCTOU yarışında ikinci kez
PumpCheck/XP yazmasını DB seviyesinde engeller (created_at-bazlı uygulama
kontrolü tek başına yarışa açıktı → çift XP). Mevcut (alembic_version'lı)
Postgres DB'leri içindir; taze DB'ler kolonu/kısıtı zaten modellerden
(db.create_all) alır ve bu revizyon stamp ile uygulanmış işaretlenir. SQLite
(test) ortamında no-op'tur. Mevcut satırlarda date_key NULL kalır — Postgres'te
NULL'lar UNIQUE içinde birbirinden ayrıdır, geçmiş kayıtlarla çakışma olmaz.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-22
"""
from alembic import op


revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: kolon + kısıt create_all'dan zaten geliyor; no-op.
        return
    op.execute('ALTER TABLE pump_check ADD COLUMN IF NOT EXISTS date_key VARCHAR(10)')
    op.execute('CREATE INDEX IF NOT EXISTS ix_pump_check_date_key ON pump_check (date_key)')
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_pump_check_day') THEN "
        "ALTER TABLE pump_check ADD CONSTRAINT uq_pump_check_day UNIQUE (user_id, date_key); "
        "END IF; END $$;"
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute('ALTER TABLE pump_check DROP CONSTRAINT IF EXISTS uq_pump_check_day')
    op.execute('DROP INDEX IF EXISTS ix_pump_check_date_key')
    op.execute('ALTER TABLE pump_check DROP COLUMN IF EXISTS date_key')
