"""add feed composite indexes (Sprint 5 remediation PR1)

Revision ID: aa77bb88cc99
Revises: ff66aa77bb88
Create Date: 2026-07-17

Feed V2 sorgularini destekleyen iki bilesik indeks (audit HIGH-1 / MEDIUM-3):

    ix_pump_check_user_created  ON pump_check (user_id, created_at)
    ix_activity_user_ts         ON activity   (user_id, timestamp)

Feed birincil kaynagi (PumpCheck) ve kilometre-tasi kaynagi (Activity) her ikisi
de `user_id IN (arkadaslar) ... ORDER BY <ts> DESC` calisir; tek-kolon indeksler
siralamayi karsilamiyordu. Activity en yuksek-hacimli tablo → 100k+ kullanicida
tam-tarama + sort maliyeti (bu revision'in ana hedefi).

Tamamen additive (expand-only): eski kod indeksleri gormeden calisir (A2 kurali).

TEKRAR-CALISTIRILABILIR: taze DB boot'u (app/db_init.py) once db.create_all()
ile modelleri kurar (indeksler artik model __table_args__'inde → create_all onlari
olusturur), sonra bu revision head'e upgrade edilir. Bu yuzden Postgres yolu
`CREATE INDEX IF NOT EXISTS` ile idempotenttir. SQLite (test) yolunda no-op'tur —
indeksler create_all'dan gelir. Mevcut (alembic_version'li) Postgres DB'lerinde
indeksler ilk kez burada olusur.

Indeks adlari SQLAlchemy'nin __table_args__'te verilen adlarla BIREBIR aynidir;
aksi halde create_all + migration iki farkli indeks yaratirdi.
"""
from alembic import op


revision = "aa77bb88cc99"
down_revision = "ff66aa77bb88"
branch_labels = None
depends_on = None


# (index_name, table, columns-SQL) — model __table_args__ ile bayt-es adlar.
_FEED_INDEXES = [
    ("ix_pump_check_user_created", "pump_check", "(user_id, created_at)"),
    ("ix_activity_user_ts",        "activity",   "(user_id, timestamp)"),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diger: indeksler create_all'dan zaten geliyor; no-op.
        return
    for name, table, cols in _FEED_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {cols}")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for name, _table, _cols in _FEED_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
