"""user FK'lerine ON DELETE CASCADE/SET NULL, UserSession index, MealLog CHECK,
referral_code -> VARCHAR(16)

Bu migration YALNIZCA mevcut (alembic_version'lı) Postgres DB'leri içindir; taze
DB'ler şemayı zaten güncel modellerden (db.create_all) alır ve bu revizyon stamp
ile "uygulanmış" işaretlenir. SQLite (test) ortamında no-op'tur.

Revision ID: a1b2c3d4e5f6
Revises: 9be792c80008
Create Date: 2026-06-16
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1b2c3d4e5f6'
down_revision = '9be792c80008'
branch_labels = None
depends_on = None


# (child_table, child_column, parent_table, parent_column, on_delete)
_FK_TARGETS = [
    ("user_session",         "user_id",        "user",        "id", "CASCADE"),
    ("weekly_log",           "user_id",        "user",        "id", "CASCADE"),
    ("weekly_check_in",      "user_id",        "user",        "id", "CASCADE"),
    ("nutrition_plan",       "user_id",        "user",        "id", "CASCADE"),
    ("training_plan",        "user_id",        "user",        "id", "CASCADE"),
    ("meal_log",             "user_id",        "user",        "id", "CASCADE"),
    ("pending_action",       "user_id",        "user",        "id", "CASCADE"),
    ("pump_check",           "user_id",        "user",        "id", "CASCADE"),
    ("friendship",           "sender_id",      "user",        "id", "CASCADE"),
    ("friendship",           "receiver_id",    "user",        "id", "CASCADE"),
    ("message",              "sender_id",      "user",        "id", "CASCADE"),
    ("message",              "receiver_id",    "user",        "id", "CASCADE"),
    ("activity",             "user_id",        "user",        "id", "CASCADE"),
    ("supplement",           "user_id",        "user",        "id", "CASCADE"),
    ("user_quest_progress",  "user_id",        "user",        "id", "CASCADE"),
    ("user_quest_progress",  "quest_id",       "daily_quest", "id", "CASCADE"),
    ("weekly_winner",        "user_id",        "user",        "id", "CASCADE"),
    ("water_log",            "user_id",        "user",        "id", "CASCADE"),
    ("workout_log",          "user_id",        "user",        "id", "CASCADE"),
    ("user_daily_nutrition", "user_id",        "user",        "id", "CASCADE"),
    ("daily_activity",       "user_id",        "user",        "id", "CASCADE"),
    ("custom_meal",          "user_id",        "user",        "id", "CASCADE"),
    ("custom_meal_item",     "custom_meal_id", "custom_meal", "id", "CASCADE"),
    # Davetçi silinirse davet edilen kullanıcı silinmemeli → SET NULL.
    ("user",                 "referred_by_id", "user",        "id", "SET NULL"),
]

# Cömert sağlık-kontrolü: negatif yok + yalnızca bozuk/taşma değerleri yakalayan
# yüksek tavan (MealLog satırı meşru olarak çok-öğeli bir toplamı tutabilir).
_MEAL_CHECK = (
    "(kalori IS NULL OR (kalori >= 0 AND kalori <= 100000)) AND "
    "(protein IS NULL OR (protein >= 0 AND protein <= 50000)) AND "
    "(karb IS NULL OR (karb >= 0 AND karb <= 50000)) AND "
    "(yag IS NULL OR (yag >= 0 AND yag <= 50000))"
)


def _existing_fk_names(conn, table, column):
    """table(column) üzerindeki mevcut FK constraint adlarını döndür (Postgres)."""
    rows = conn.execute(sa.text(
        """
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE con.contype = 'f'
          AND rel.relname = :table
          AND con.conkey = ARRAY[(
              SELECT attnum FROM pg_attribute
              WHERE attrelid = rel.oid AND attname = :column AND NOT attisdropped
          )]::smallint[]
        """
    ), {"table": table, "column": column}).fetchall()
    return [r[0] for r in rows]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite/diğer: taze şema create_all'dan zaten ondelete ile gelir; no-op.
        return

    # 1) referral_code'u 16 karaktere genişlet (yüksek-entropi davet kodları).
    op.execute('ALTER TABLE "user" ALTER COLUMN referral_code TYPE VARCHAR(16)')

    # 2) UserSession.user_id index'i (her coach/tracking sorgusu buna filtreliyor).
    op.execute('CREATE INDEX IF NOT EXISTS ix_user_session_user_id ON user_session (user_id)')

    # 3) Tüm child FK'lerini istenen ON DELETE davranışıyla yeniden kur (idempotent:
    #    önce mevcut adsız/eski constraint'i bul-bırak, sonra adlı yenisini ekle).
    for table, column, parent_t, parent_c, on_delete in _FK_TARGETS:
        for conname in _existing_fk_names(bind, table, column):
            op.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{conname}"')
        new_name = f"{table}_{column}_fkey"
        op.execute(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_name}" '
            f'FOREIGN KEY ("{column}") REFERENCES "{parent_t}" ("{parent_c}") '
            f'ON DELETE {on_delete}'
        )

    # 4) MealLog makro CHECK: önce (ender) ihlalleri sınırlara kıs, sonra constraint.
    op.execute("UPDATE meal_log SET kalori = 100000 WHERE kalori > 100000")
    op.execute("UPDATE meal_log SET kalori = 0 WHERE kalori < 0")
    op.execute("UPDATE meal_log SET protein = 50000 WHERE protein > 50000")
    op.execute("UPDATE meal_log SET protein = 0 WHERE protein < 0")
    op.execute("UPDATE meal_log SET karb = 50000 WHERE karb > 50000")
    op.execute("UPDATE meal_log SET karb = 0 WHERE karb < 0")
    op.execute("UPDATE meal_log SET yag = 50000 WHERE yag > 50000")
    op.execute("UPDATE meal_log SET yag = 0 WHERE yag < 0")
    op.execute('ALTER TABLE meal_log DROP CONSTRAINT IF EXISTS ck_meal_log_macro_bounds')
    op.execute(f'ALTER TABLE meal_log ADD CONSTRAINT ck_meal_log_macro_bounds CHECK ({_MEAL_CHECK})')


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute('ALTER TABLE meal_log DROP CONSTRAINT IF EXISTS ck_meal_log_macro_bounds')

    # FK'leri ON DELETE kuralı olmadan (eski hâl) geri kur.
    for table, column, parent_t, parent_c, _on_delete in _FK_TARGETS:
        for conname in _existing_fk_names(bind, table, column):
            op.execute(f'ALTER TABLE "{table}" DROP CONSTRAINT "{conname}"')
        new_name = f"{table}_{column}_fkey"
        op.execute(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{new_name}" '
            f'FOREIGN KEY ("{column}") REFERENCES "{parent_t}" ("{parent_c}")'
        )

    op.execute('DROP INDEX IF EXISTS ix_user_session_user_id')
    op.execute('ALTER TABLE "user" ALTER COLUMN referral_code TYPE VARCHAR(12)')
