"""Eksik sosyal indeksler: message/friendship sender_id & receiver_id.

social.py, mesaj/arkadaşlık sorgularını OR(sender_id==me, receiver_id==me) ve
filter_by(receiver_id=...) ile yoğun kullanır; bu kolonlar indekssizdi. Postgres'te
FK otomatik indeks oluşturmaz (TRIAGE_FIXES #5).

Mevcut (alembic_version'lı) Postgres DB'leri içindir; taze DB'ler indeksleri zaten
modellerden (db.create_all + index=True) alır. İndeks adları SQLAlchemy'nin
varsayılan ix_<tablo>_<kolon> şemasıyla aynıdır; CREATE INDEX IF NOT EXISTS
idempotenttir. SQLite (test) ortamında no-op'tur.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-19
"""
from alembic import op


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


# (index_name, table, columns-SQL) — sosyal sorgularda eksik indeksler.
_SOCIAL_INDEXES = [
    ("ix_message_sender_id",      "message",    "(sender_id)"),
    ("ix_message_receiver_id",    "message",    "(receiver_id)"),
    ("ix_friendship_sender_id",   "friendship", "(sender_id)"),
    ("ix_friendship_receiver_id", "friendship", "(receiver_id)"),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: indeksler create_all'dan zaten geliyor; no-op.
        return
    for name, table, cols in _SOCIAL_INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, _table, _cols in _SOCIAL_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS {name}')
