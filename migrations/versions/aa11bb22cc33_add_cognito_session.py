"""add cognito_session

Revision ID: aa11bb22cc33
Revises: f8a9b0c1d2e3
Create Date: 2026-07-10

Sprint 2: oturuma bağlı Cognito token'ları için sunucu tarafı tablo. Additive
(expand-only) — rollback kodu geri alsa da tablo kalır, eski kod onsuz çalışır.

Not: başta d6e7f8a9b0c1'e zincirlenmişti; barcode işi main'e indikten (PR #133)
sonra main'in birleştirme head'i f8a9b0c1d2e3 (e7f8a9b0c1d2 + d6e7f8a9b0c1) oldu.
Tek doğrusal zincir için down_revision buna güncellendi (f8a9b0c1d2e3 zaten
d6e7f8a9b0c1'in torunu, bağımlılık korunuyor).
"""
from alembic import op
import sqlalchemy as sa

revision = "aa11bb22cc33"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cognito_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cognito_username", sa.String(length=80), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token_exp", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_cognito_session_user_id", "cognito_session", ["user_id"])
    op.create_index("uq_cognito_session_session_id", "cognito_session", ["session_id"], unique=True)


def downgrade():
    op.drop_index("uq_cognito_session_session_id", table_name="cognito_session")
    op.drop_index("ix_cognito_session_user_id", table_name="cognito_session")
    op.drop_table("cognito_session")
