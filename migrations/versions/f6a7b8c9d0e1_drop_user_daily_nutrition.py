"""drop legacy user_daily_nutrition table

UserDailyNutrition artık ne yazılıyor ne okunuyor — beslenme TEK kanonik
defterde tutuluyor (MealLog). Eski koç öğünleri df0d08c0cd24 ile MealLog'a
taşındı; tablo bir sürüm güvenlik ağı olarak tutulmuştu, şimdi düşürülüyor.

DB-agnostik: DROP TABLE IF EXISTS (Postgres prod + SQLite dev). Taze DB'lerde
model kaldırıldığı için tablo zaten create_all'da oluşmaz ve bu revizyon boot'ta
stamp'lenir (çalışmaz); yalnızca mevcut DB'lerde tabloyu gerçekten düşürür.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TABLE IF EXISTS user_daily_nutrition")


def downgrade():
    # Geri alma: boş tabloyu yeniden kur. Taşınan veriler geri yüklenmez
    # (df0d08c0cd24.downgrade zaten no-op — kaynak/yeni koç öğünleri ayırt
    # edilemediği için).
    op.create_table(
        "user_daily_nutrition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("food_item", sa.String(length=200), nullable=False),
        sa.Column("calories", sa.Float(), nullable=False, server_default="0"),
        sa.Column("protein", sa.Float(), nullable=False, server_default="0"),
        sa.Column("carbs", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fat", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_daily_nutrition_user_id",
                    "user_daily_nutrition", ["user_id"])
    op.create_index("ix_user_daily_nutrition_created_at",
                    "user_daily_nutrition", ["created_at"])
