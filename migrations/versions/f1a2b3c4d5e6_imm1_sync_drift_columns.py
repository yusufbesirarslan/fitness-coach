"""I-M1: drifted freemium kolonlarını zincire al + tek-seferlik veri düzeltmeleri

Eskiden bu kolonlar YALNIZCA app/db_init.py'deki ham ALTER ile (ve fresh DB'lerde
create_all ile) ekleniyordu; migration zinciri model'in gerisinde kalıyor, CI
schema-drift guard'ı (`flask db check`) kırmızı oluyordu. Bu migration zinciri
model'e eşitler. İdempotent: prod'da ham ALTER kolonu zaten eklemiş olabilir,
o yüzden kolon varsa atlanır (inspector ile dialect-bağımsız kontrol).

Ayrıca db_init.py'de HER boot'ta çalışan iki ham UPDATE (is_claimed legacy
backfill + quest başlıklarını TR'leştirme) buraya, tek-seferlik olarak taşındı.

Revision ID: f1a2b3c4d5e6
Revises: c4d5e6f7a8b9
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def _has_column(table, column):
    """Kolon var mı? (Postgres + SQLite). İdempotency: prod'da ham ALTER zaten
    eklemiş olabilir → tekrar ADD COLUMN 'already exists' ile patlamasın."""
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    # ── Freemium kolonları (model'de var, zincirde yoktu) ──
    if not _has_column("user", "is_premium"):
        op.add_column("user", sa.Column(
            "is_premium", sa.Boolean(), server_default=sa.text("false"), nullable=True))
    if not _has_column("user", "premium_since"):
        op.add_column("user", sa.Column("premium_since", sa.DateTime(), nullable=True))
    if not _has_column("user", "profile_picture_key"):
        op.add_column("user", sa.Column(
            "profile_picture_key", sa.String(length=300), nullable=True))

    # ── Tek-seferlik veri düzeltmeleri (eskiden her boot'ta çalışıyordu) ──
    bind = op.get_bind()
    # Legacy is_claimed=false satırlarını kapat. Yeni satırlar zaten is_claimed=True
    # ile yazılır (gamification.complete_quest); bu yalnızca eski stragglers içindir.
    bind.execute(sa.text(
        "UPDATE user_quest_progress SET is_claimed = true WHERE is_claimed = false"))
    # Eski İngilizce quest başlıklarını TR'ye sabitle (yeni seed zaten TR).
    for qtype, title in [
        ("login", "Günlük Giriş"),
        ("workout_logged", "Antrenman Kaydet"),
        ("suggestion_sent", "Bir Arkadaşına Yardım Et"),
        ("supplement_added", "Dolabını Güncelle"),
        ("meal_logged", "Öğün Kaydet"),
    ]:
        bind.execute(
            sa.text("UPDATE daily_quest SET title = :t WHERE quest_type = :q"),
            {"t": title, "q": qtype})


def downgrade():
    op.drop_column("user", "profile_picture_key")
    op.drop_column("user", "premium_since")
    op.drop_column("user", "is_premium")
