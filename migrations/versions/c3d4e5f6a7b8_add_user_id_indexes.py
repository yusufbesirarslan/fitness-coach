"""Eksik user_id indeksleri + MealLog (user_id, tarih) kompoziti.

Mevcut (alembic_version'lı) Postgres DB'leri içindir; taze DB'ler indeksleri zaten
modellerden (db.create_all) alır ve bu revizyon stamp ile uygulanmış işaretlenir.
SQLite (test) ortamında no-op'tur.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-17
"""
from alembic import op


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


# (index_name, table, columns-SQL) — user_id FK'lerinde eksik indeksler.
_USER_ID_INDEXES = [
    ("ix_weekly_log_user_id",      "weekly_log",      "(user_id)"),
    ("ix_weekly_check_in_user_id", "weekly_check_in", "(user_id)"),
    ("ix_nutrition_plan_user_id",  "nutrition_plan",  "(user_id)"),
    ("ix_training_plan_user_id",   "training_plan",   "(user_id)"),
    ("ix_pump_check_user_id",      "pump_check",      "(user_id)"),
    ("ix_supplement_user_id",      "supplement",      "(user_id)"),
    # MealLog: en çok yazılan defter; kompozit (user_id, tarih) — user_id-only da kapsar.
    ("ix_meal_log_user_id_tarih",  "meal_log",        "(user_id, tarih)"),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: indeksler create_all'dan zaten geliyor; no-op.
        return
    for name, table, cols in _USER_ID_INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, _table, _cols in _USER_ID_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS {name}')
