"""Expand the canonical check-in with its owner-private review operation.

Revision ID: e6a4b91d702c
Revises: c8e1f3a5b702
"""
from alembic import op
import sqlalchemy as sa

from app.weekly_review_schema import STATUS_CHECK, STATUS_RULE, WORK_INDEX, WORK_PREDICATE

revision = "e6a4b91d702c"
down_revision = "c8e1f3a5b702"
branch_labels = None
depends_on = None
expand_contract = "expand"

TABLE = "weekly_check_in"
COLUMNS = (
    ("review_status", sa.String(12)),
    ("review_context", sa.JSON()),
    ("review_updated_at", sa.DateTime()),
    ("review_result", sa.JSON()),
    ("review_evidence_digest", sa.String(64)),
    ("review_failure", sa.String(24)),
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {col["name"]: col for col in inspector.get_columns(TABLE)}
    present = set(existing) & {name for name, _ in COLUMNS}
    if present:
        # Local schema bootstrap can have created the current model already.
        checks = {item["name"] for item in inspector.get_check_constraints(TABLE)}
        indexes = {item["name"] for item in inspector.get_indexes(TABLE)}
        if (present != {name for name, _ in COLUMNS} or STATUS_CHECK not in checks
                or WORK_INDEX not in indexes
                or any(not existing[name]["nullable"] or existing[name]["default"] is not None
                       or not isinstance(existing[name]["type"], type(kind))
                       or getattr(existing[name]["type"], "length", None)
                       != getattr(kind, "length", None) for name, kind in COLUMNS)):
            raise RuntimeError("Weekly check-in review schema mismatch")
        return
    with op.batch_alter_table(TABLE) as batch:
        for name, kind in COLUMNS:
            batch.add_column(sa.Column(name, kind))
        batch.create_check_constraint(STATUS_CHECK, STATUS_RULE)
        batch.create_index(WORK_INDEX, ["review_status", "review_updated_at", "id"],
                           postgresql_where=sa.text(WORK_PREDICATE),
                           sqlite_where=sa.text(WORK_PREDICATE))


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE weekly_check_in IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(
            "SELECT 1 FROM weekly_check_in WHERE review_status IS NOT NULL LIMIT 1")).scalar():
        raise RuntimeError("PD6 downgrade would discard existing weekly review operations")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_index(WORK_INDEX)
        batch.drop_constraint(STATUS_CHECK, type_="check")
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)
