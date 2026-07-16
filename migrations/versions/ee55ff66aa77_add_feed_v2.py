"""add feed v2 (Sprint 5 PR2)

Revision ID: ee55ff66aa77
Revises: dd44ee55ff66
Create Date: 2026-07-16

Feed V2 omurgasi: FeedItem (repost/quote) + FeedItemLike/FeedItemComment
(quote begeni/yorum) + FeedHide/FeedReport (goruntuleyen-basi moderasyon) +
PumpCheck.reposts_count denormalize sayac. Tamamen additive (expand-only) —
rollback kodu geri alsa da tablolar/kolon kalir, eski kod onlari hic gormeden
calisir (A2 kurali).

TEKRAR-CALISTIRILABILIR: taze DB boot'u (app/db_init.py) once db.create_all()
ile modelleri kurar, sonra dd44ee55ff66'yi damgalayip head'e upgrade eder —
yani bu revision create_all'in ZATEN yarattigi tablolara/kolona karsi da kosar.
Her create_table has_table kapisiyla, reposts_count kolon-varligi kapisiyla
korunur. Prod Postgres'te ve CI drift job'inda yoktur → normal olusturulur.
"""
from alembic import op
import sqlalchemy as sa

revision = "ee55ff66aa77"
down_revision = "dd44ee55ff66"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("feed_item"):
        op.create_table(
            "feed_item",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("item_type", sa.String(length=20), nullable=False),
            sa.Column("ref_type", sa.String(length=20), nullable=False, server_default="pump_check"),
            sa.Column("ref_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.String(length=500), nullable=True),
            sa.Column("likes_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("comments_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "item_type", "ref_type", "ref_id", name="uq_feed_item_user_ref"),
        )
        op.create_index("ix_feed_item_user_id", "feed_item", ["user_id"])
        op.create_index("ix_feed_item_item_type", "feed_item", ["item_type"])
        op.create_index("ix_feed_item_ref_id", "feed_item", ["ref_id"])
        op.create_index("ix_feed_item_created_at", "feed_item", ["created_at"])

    if not insp.has_table("feed_item_like"):
        op.create_table(
            "feed_item_like",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("feed_item_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["feed_item_id"], ["feed_item.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("feed_item_id", "user_id", name="uq_feed_item_like_user"),
        )
        op.create_index("ix_feed_item_like_feed_item_id", "feed_item_like", ["feed_item_id"])
        op.create_index("ix_feed_item_like_user_id", "feed_item_like", ["user_id"])
        op.create_index("ix_feed_item_like_created_at", "feed_item_like", ["created_at"])

    if not insp.has_table("feed_item_comment"):
        op.create_table(
            "feed_item_comment",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("feed_item_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("body", sa.String(length=500), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["feed_item_id"], ["feed_item.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_feed_item_comment_feed_item_id", "feed_item_comment", ["feed_item_id"])
        op.create_index("ix_feed_item_comment_user_id", "feed_item_comment", ["user_id"])
        op.create_index("ix_feed_item_comment_created_at", "feed_item_comment", ["created_at"])

    if not insp.has_table("feed_hide"):
        op.create_table(
            "feed_hide",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=20), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_hide_target"),
        )
        op.create_index("ix_feed_hide_user_id", "feed_hide", ["user_id"])

    if not insp.has_table("feed_report"):
        op.create_table(
            "feed_report",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_type", sa.String(length=20), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=30), nullable=False),
            sa.Column("note", sa.String(length=300), nullable=True),
            sa.Column("status", sa.String(length=15), nullable=False, server_default="open"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "target_type", "target_id", name="uq_feed_report_target"),
        )
        op.create_index("ix_feed_report_user_id", "feed_report", ["user_id"])

    cols = {c["name"] for c in insp.get_columns("pump_check")}
    if "reposts_count" not in cols:
        op.add_column("pump_check", sa.Column("reposts_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("pump_check")}
    if "reposts_count" in cols:
        op.drop_column("pump_check", "reposts_count")
    for table in ("feed_report", "feed_hide", "feed_item_comment", "feed_item_like", "feed_item"):
        if insp.has_table(table):
            op.drop_table(table)
