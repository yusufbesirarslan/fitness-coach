"""daily_activity: günde tek satır — uq_daily_activity kısıdından intensity'yi çıkar (C4)

Eski kısıt (user_id, date_key, intensity) "günde tam bir satır" invariantını
zorlamıyordu: farklı intensity'li iki eşzamanlı istek ikisi de sil-ekle yapıp
İKİ satır bırakabiliyordu. Önce olası mükerrer günleri temizle (en yeni id
kazanır — today_activity zaten id.desc() ile en yenisini gösteriyordu), sonra
kısıdı (user_id, date_key) olarak yeniden kur.

Revision ID: c5d6e7f8a9b0
Revises: b1c2d3e4f5a6
Create Date: 2026-07-04
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    # Mükerrer günleri temizle: (user_id, date_key) başına en büyük id'li satır kalır.
    op.execute(
        "DELETE FROM daily_activity WHERE id NOT IN ("
        "SELECT MAX(id) FROM daily_activity GROUP BY user_id, date_key)"
    )
    # batch_alter_table: Postgres'te düz ALTER, SQLite'ta tablo-yeniden-kurma.
    with op.batch_alter_table("daily_activity") as batch_op:
        batch_op.drop_constraint("uq_daily_activity", type_="unique")
        batch_op.create_unique_constraint(
            "uq_daily_activity", ["user_id", "date_key"])


def downgrade():
    with op.batch_alter_table("daily_activity") as batch_op:
        batch_op.drop_constraint("uq_daily_activity", type_="unique")
        batch_op.create_unique_constraint(
            "uq_daily_activity", ["user_id", "date_key", "intensity"])
