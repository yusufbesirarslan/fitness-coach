"""Add prospective schedule coverage and dedicated PD6 accounting.

Revision ID: f14209ba73c6
Revises: e6a4b91d702c (isolated qualification parent)
"""
from datetime import timedelta
import json

from alembic import op
import sqlalchemy as sa

from app.services.workout_state.queries import schedule_tips
from app.timeutil import app_today

revision = "f14209ba73c6"
down_revision = "e6a4b91d702c"
branch_labels = None
depends_on = None
expand_contract = "expand"


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = {item["name"]: item for item in inspector.get_columns("weekly_check_in")}
    coverage_exists = inspector.has_table("workout_schedule_coverage")
    admission_exists = inspector.has_table("weekly_review_admission")
    admission_column = columns.get("review_admission_week")
    if coverage_exists or admission_exists or admission_column is not None:
        # Development bootstrap can create current ORM tables before Alembic.
        if (not coverage_exists or not admission_exists
                or admission_column is None or not admission_column["nullable"]
                or not isinstance(admission_column["type"], sa.Date)):
            raise RuntimeError("Incomplete retention and weekly review schema")
    else:
        op.create_table(
            "workout_schedule_coverage",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("effective_date", sa.Date(), primary_key=True),
            sa.Column("lineage_id", sa.String(64)),
            sa.Column("schedule", sa.JSON(none_as_null=True)),
        )
        op.create_table(
            "weekly_review_admission",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("week_start", sa.Date(), primary_key=True),
            sa.Column("window_start", sa.Date(), nullable=False),
            sa.Column("state", sa.String(8), nullable=False),
            sa.Column("physical_attempt", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.CheckConstraint("state IN ('reserved', 'charged', 'restored')",
                               name="ck_weekly_review_admission_state"),
        )
        op.create_index("ix_weekly_review_admission_window", "weekly_review_admission",
                        ["user_id", "window_start"])
        op.add_column("weekly_check_in", sa.Column("review_admission_week", sa.Date()))

    # Qualify with old plan writers quiesced until coverage-aware writers take
    # over. The migration does not lock the existing TrainingPlan table or
    # reject writes from the serving revision.
    first_day = app_today() + timedelta(days=1)
    rows = connection.execute(sa.text("""
        SELECT user_id, lineage_id, plan_data FROM (
          SELECT user_id, lineage_id, plan_data, ROW_NUMBER() OVER (
            PARTITION BY user_id ORDER BY created_at DESC, id DESC
          ) AS active_rank FROM training_plan
        ) AS ranked WHERE active_rank = 1
    """)).mappings()
    for batch in rows.partitions(500):
        connection.execute(sa.text("""
            INSERT INTO workout_schedule_coverage (user_id, effective_date, lineage_id, schedule)
            VALUES (:user_id, :effective_date, :lineage_id, :schedule)
            ON CONFLICT (user_id, effective_date) DO NOTHING
        """), [
            dict(user_id=row["user_id"], effective_date=first_day,
                 lineage_id=row["lineage_id"],
                 schedule=json.dumps(schedule_tips(row["plan_data"]) or []))
            for row in batch
        ])


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE weekly_check_in IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM weekly_review_admission LIMIT 1")).scalar():
        raise RuntimeError("Downgrade would discard dedicated weekly review accounting")
    op.drop_column("weekly_check_in", "review_admission_week")
    op.drop_table("weekly_review_admission")
    op.drop_table("workout_schedule_coverage")
