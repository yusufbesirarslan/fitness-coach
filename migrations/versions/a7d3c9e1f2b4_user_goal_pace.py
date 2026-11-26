"""Store the owner's chosen goal pace (PD1, #547).

Revision ID: a7d3c9e1f2b4
Revises: 5cc5aa7e47ba

Expand only: one nullable column, ``user.goal_pace``. NULL means "never
chosen" and keeps the legacy daily-target formula, so existing rows need no
backfill and pre-PD1 code keeps working against this schema (it never reads
the column). Downgrade refuses to discard a chosen pace; with paces present,
roll back code and keep the schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "a7d3c9e1f2b4"
down_revision = "5cc5aa7e47ba"
branch_labels = None
depends_on = None
expand_contract = "expand"

TABLE = "user"
COLUMN = "goal_pace"


def upgrade():
    bind = op.get_bind()
    existing = {c["name"]: c for c in sa.inspect(bind).get_columns(TABLE)}.get(COLUMN)
    if existing is None:
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(16), nullable=True))
    elif (existing["type"].compile(dialect=bind.dialect)
          != sa.String(16).compile(dialect=bind.dialect)
          or existing["nullable"] is not True or existing.get("default") is not None):
        raise RuntimeError("incompatible user.goal_pace")


def downgrade():
    bind = op.get_bind()
    retained = bind.execute(sa.text(
        'SELECT count(*) FROM "user" WHERE goal_pace IS NOT NULL')).scalar()
    if retained:
        # An owner's chosen pace decides their calorie target; keep it.
        raise RuntimeError("chosen goal paces must be retained")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column(COLUMN)
