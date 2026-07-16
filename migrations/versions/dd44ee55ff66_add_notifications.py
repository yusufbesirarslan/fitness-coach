"""add notifications (Sprint 5 PR1)

Revision ID: dd44ee55ff66
Revises: cc33dd44ee55
Create Date: 2026-07-16

Sosyal bildirim tablosu: begeni/yorum/arkadaslik olaylarinda alici kullaniciya
satir yazilir (app/services/notifications.py). Tamamen additive (expand-only) —
rollback kodu geri alsa da tablo kalir, eski kod onu hic gormeden calisir
(A2 kurali).

TEKRAR-CALISTIRILABILIR (has_table kapisi): taze DB boot'u (app/db_init.py)
once db.create_all() ile modelleri kurar, sonra aa11bb22cc33'u damgalayip
head'e upgrade eder — yani bu revision create_all'in ZATEN yarattigi tabloya
karsi da kosar. Prod Postgres'te ve CI drift job'inda tablo yoktur → normal
sekilde olusturulur.
"""
from alembic import op
import sqlalchemy as sa

revision = "dd44ee55ff66"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None


def _has(table):
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade():
    if _has("notification"):
        return
    bind = op.get_bind()
    payload_type = sa.JSON()
    if bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        payload_type = JSONB()
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("ntype", sa.String(length=30), nullable=False),
        sa.Column("target_type", sa.String(length=20), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("payload", payload_type, nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_notification_user_id", "notification", ["user_id"])
    op.create_index("ix_notification_actor_id", "notification", ["actor_id"])
    op.create_index("ix_notification_created_at", "notification", ["created_at"])
    op.create_index("ix_notification_user_read", "notification", ["user_id", "is_read"])


def downgrade():
    op.drop_index("ix_notification_user_read", table_name="notification")
    op.drop_index("ix_notification_created_at", table_name="notification")
    op.drop_index("ix_notification_actor_id", table_name="notification")
    op.drop_index("ix_notification_user_id", table_name="notification")
    op.drop_table("notification")
