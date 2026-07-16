"""add challenges (Sprint 5 PR3)

Revision ID: ff66aa77bb88
Revises: ee55ff66aa77
Create Date: 2026-07-16

Challenge katalogu + UserChallengeProgress (per-user-per-period ilerleme) +
UserBadge (tek-seferlik rozet). Tamamen additive (expand-only) — rollback kodu
geri alsa da tablolar kalir, eski kod onlari gormeden calisir (A2 kurali).

TEKRAR-CALISTIRILABILIR: taze DB boot'u (app/db_init.py) once db.create_all()
ile modelleri kurar, sonra ee55ff66aa77'yi damgalayip head'e upgrade eder — yani
bu revision create_all'in ZATEN yarattigi tablolara karsi da kosar. Her
create_table has_table kapisiyla korunur. Prod Postgres'te ve CI drift job'inda
yoktur → normal olusturulur.
"""
from alembic import op
import sqlalchemy as sa

revision = "ff66aa77bb88"
down_revision = "ee55ff66aa77"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("challenge"):
        op.create_table(
            "challenge",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("category", sa.String(length=30), nullable=True),
            sa.Column("metric", sa.String(length=30), nullable=False),
            sa.Column("target_value", sa.Integer(), nullable=False),
            sa.Column("xp_reward", sa.Integer(), nullable=False),
            sa.Column("badge_code", sa.String(length=50), nullable=True),
            sa.Column("challenge_type", sa.String(length=20), nullable=False, server_default="global"),
            sa.Column("period_type", sa.String(length=20), nullable=False, server_default="weekly"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_challenge_metric", "challenge", ["metric"])

    if not insp.has_table("user_challenge_progress"):
        op.create_table(
            "user_challenge_progress",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("period_key", sa.String(length=10), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("opted_in", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["challenge_id"], ["challenge.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "challenge_id", "period_key", name="uq_user_challenge_period"),
        )
        op.create_index("ix_user_challenge_progress_user_id", "user_challenge_progress", ["user_id"])
        op.create_index("ix_user_challenge_progress_challenge_id", "user_challenge_progress", ["challenge_id"])
        op.create_index("ix_user_challenge_progress_period_key", "user_challenge_progress", ["period_key"])
        op.create_index("ix_ucp_challenge_period", "user_challenge_progress", ["challenge_id", "period_key"])

    if not insp.has_table("user_badge"):
        op.create_table(
            "user_badge",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("badge_code", sa.String(length=50), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=True),
            sa.Column("earned_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "badge_code", name="uq_user_badge"),
        )
        op.create_index("ix_user_badge_user_id", "user_badge", ["user_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in ("user_badge", "user_challenge_progress", "challenge"):
        if insp.has_table(table):
            op.drop_table(table)
