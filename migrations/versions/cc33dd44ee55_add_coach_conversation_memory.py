"""add coach conversation memory (Sprint 4 WS1)

Revision ID: cc33dd44ee55
Revises: bb22cc33dd44
Create Date: 2026-07-14

Kalıcı AI koç hafızası: coach_conversation (oturum + LLM özeti) ve
coach_message (tur tur mesajlar). Tamamen additive (expand-only) — rollback
kodu geri alsa da tablolar kalır, eski kod onları hiç görmeden çalışır
(A2 kuralı). Mesajlar SİLİNMEZ; reset yalnızca archived_at damgalar.

'Kullanıcı başına tek aktif konuşma' kısıtı bilinçli olarak kodda tutuldu
(memory_manager.get_or_create_active_conversation, User satır kilidiyle):
kısmi (WHERE archived_at IS NULL) unique indeks SQLite'ta desteklenmiyor,
lokal geliştirme ile prod şeması ayrışmasın.

TEKRAR-ÇALIŞTIRILABİLİR (has_table kapısı): taze DB boot'u (app/db_init.py)
önce db.create_all() ile modelleri kurar, sonra aa11bb22cc33'ü damgalayıp
head'e upgrade eder — yani bu revision create_all'ın ZATEN yarattığı tablolara
karşı da koşar. Prod Postgres'te ve CI drift job'ında tablolar yoktur → normal
şekilde oluşturulur. aa11bb22cc33 sonrası her migration bu invariantı sağlamalı.
"""
from alembic import op
import sqlalchemy as sa

revision = "cc33dd44ee55"
down_revision = "bb22cc33dd44"
branch_labels = None
depends_on = None


def _has(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    if not _has("coach_conversation"):
        _create_conversation()
    if not _has("coach_message"):
        _create_message()


def _create_conversation():
    op.create_table(
        "coach_conversation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summarized_upto_id", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_coach_conversation_user_id", "coach_conversation", ["user_id"])
    op.create_index("ix_coach_conversation_archived_at", "coach_conversation", ["archived_at"])


def _create_message():
    op.create_table(
        "coach_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=12), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("interrupted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["coach_conversation.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_coach_message_conversation_id", "coach_message", ["conversation_id"])
    op.create_index("ix_coach_message_created_at", "coach_message", ["created_at"])


def downgrade():
    op.drop_index("ix_coach_message_created_at", table_name="coach_message")
    op.drop_index("ix_coach_message_conversation_id", table_name="coach_message")
    op.drop_table("coach_message")
    op.drop_index("ix_coach_conversation_archived_at", table_name="coach_conversation")
    op.drop_index("ix_coach_conversation_user_id", table_name="coach_conversation")
    op.drop_table("coach_conversation")
