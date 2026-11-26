"""Expand WeeklyCheckIn with versioned nutrition adherence.

Revision ID: c8e1f3a5b702
Revises: b7d2e9f4a1c3
"""
from alembic import op
import sqlalchemy as sa

revision = "c8e1f3a5b702"
down_revision = "b7d2e9f4a1c3"
branch_labels = None
depends_on = None
expand_contract = "expand"

TABLE = "weekly_check_in"
COLUMN = "nutrition_adherence_v2"
CONSTRAINT = "ck_weekly_checkin_nutrition_adherence_v2"
RULE = (
    "nutrition_adherence_v2 IS NULL OR (beslenme_uyumu IS NULL AND "
    "yogunluk IS NOT NULL AND nutrition_adherence_v2 IN "
    "('yes', 'partly', 'no', 'not_applicable'))"
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    if COLUMN not in columns:
        op.add_column(TABLE, sa.Column(
            COLUMN, sa.String(14), sa.CheckConstraint(RULE, name=CONSTRAINT)))
        return
    column = columns[COLUMN]
    checks = {check["name"] for check in inspector.get_check_constraints(TABLE)}
    if (not isinstance(column["type"], sa.String) or column["type"].length != 14
            or not column["nullable"] or column["default"] is not None
            or CONSTRAINT not in checks):
        raise RuntimeError("Weekly check-in adherence schema mismatch")


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE weekly_check_in IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(
            "SELECT 1 FROM weekly_check_in WHERE nutrition_adherence_v2 IS NOT NULL LIMIT 1"
    )).scalar():
        raise RuntimeError("PD5 downgrade would discard recorded adherence")
    op.drop_column(TABLE, COLUMN)
