"""backfill user_daily_nutrition to meal_log

Revision ID: df0d08c0cd24
Revises: 54f2eb195404
Create Date: 2026-06-15 20:00:19.514690

"""
from datetime import datetime

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = 'df0d08c0cd24'
down_revision = '54f2eb195404'
branch_labels = None
depends_on = None


def upgrade():
    """UserDailyNutrition (yalnızca AI koç yazıyordu) satırlarını tek kanonik
    beslenme defterine (MealLog) taşı. Faz B: koç artık doğrudan MealLog yazıyor;
    bu geçiş eski koç öğünlerini de aynı deftere alır ki 'bugün tüketilen', kalan
    bütçe, haftalık rapor ve protein dürtüsü TÜM giriş yollarını görsün.

    DB-agnostik (Postgres prod + SQLite dev): tarih anahtarı Python'da üretilir.
    user_daily_nutrition tablosu güvenlik ağı olarak BIR sürüm korunur (silinmez).
    """
    bind = op.get_bind()
    rows = bind.execute(text(
        "SELECT user_id, food_item, calories, protein, carbs, fat, created_at "
        "FROM user_daily_nutrition"
    )).fetchall()

    insert = text(
        "INSERT INTO meal_log "
        "(user_id, ogun, yemekler, kalori, protein, karb, yag, tarih, source, created_at) "
        "VALUES (:uid, :ogun, :yem, :kal, :pro, :karb, :yag, :tarih, :src, :created)"
    )
    for r in rows:
        created = r.created_at
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created)
            except ValueError:
                created = datetime.utcnow()
        elif created is None:
            created = datetime.utcnow()
        bind.execute(insert, {
            "uid": r.user_id,
            "ogun": "AI Koç",
            "yem": r.food_item,
            "kal": r.calories,
            "pro": r.protein,
            "karb": r.carbs,
            "yag": r.fat,
            "tarih": created.strftime("%d.%m"),
            "src": "coach",
            "created": created,
        })


def downgrade():
    # Geri alınamaz: taşınan koç öğünleri ile geçiş SONRASI yazılan yeni koç
    # öğünleri (ikisi de source='coach') güvenle ayırt edilemez; veri kaybı
    # riskini almamak için no-op. Kaynak tablo (user_daily_nutrition) hâlâ duruyor.
    pass
