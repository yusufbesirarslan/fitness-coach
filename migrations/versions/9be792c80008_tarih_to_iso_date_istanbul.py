"""tarih to ISO date (Istanbul)

Revision ID: 9be792c80008
Revises: df0d08c0cd24
Create Date: 2026-06-15 20:18:37.700867

"""
from datetime import datetime
from zoneinfo import ZoneInfo

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '9be792c80008'
down_revision = 'df0d08c0cd24'
branch_labels = None
depends_on = None

_APP_TZ = ZoneInfo("Europe/Istanbul")
_UTC = ZoneInfo("UTC")


def upgrade():
    """meal_log.tarih'i yıl-içermeyen '%d.%m'den ISO 'YYYY-MM-DD'ye taşı (F4).

    Eski tarih yıl içermediği için ay/yıl çakışmaları oluyordu ('15.06' hem 2025
    hem 2026 Haziran). Doğru tarih created_at'ten (yıl dahil) Istanbul gününe
    çevrilerek yeniden türetilir (F5: tüm gün anahtarları Istanbul). Idempotent:
    tekrar çalışsa aynı ISO değeri üretir.
    """
    bind = op.get_bind()
    rows = bind.execute(text("SELECT id, created_at FROM meal_log")).fetchall()
    upd = text("UPDATE meal_log SET tarih = :tarih WHERE id = :id")
    for r in rows:
        created = r.created_at
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except ValueError:
                continue
        if created is None:
            continue
        # created_at naive UTC olarak saklanır → Istanbul gününe çevir.
        ist = created.replace(tzinfo=_UTC).astimezone(_APP_TZ)
        bind.execute(upd, {"tarih": ist.date().isoformat(), "id": r.id})


def downgrade():
    # ISO → '%d.%m' geri dönüşü kayıplı (yıl atılır) ve gereksiz; no-op.
    pass
