"""Durable physical-attempt evidence for metered native Training generation.

MON-02-BE-03B-A1. Expand only: ``training_plan_generation_operation.
provider_attempt_possible`` (nullable). ``provider_dispatch_possible`` says the
operation was AUTHORIZED to call a provider; this column says whether a
physical SDK invocation may have STARTED. A metered dispatch commits it False;
every SDK invocation is preceded by a committed True (after the final consent
check). NULL = not tracked (unmetered, historical, or written by older code),
which keeps today's UNKNOWN settlement.

No backfill: existing operations stay NULL.
"""
from alembic import op
import sqlalchemy as sa

revision = "e7a1c3d5f9b2"
down_revision = "b1e4f5a6c7d8"
branch_labels = None
depends_on = None
expand_contract = "expand"

OPERATION = "training_plan_generation_operation"
COLUMN = "provider_attempt_possible"


def upgrade():
    bind = op.get_bind()
    existing = {c["name"]: c for c in sa.inspect(bind).get_columns(OPERATION)}.get(COLUMN)
    if existing is None:
        op.add_column(OPERATION, sa.Column(COLUMN, sa.Boolean(), nullable=True))
    elif (existing["type"].compile(dialect=bind.dialect)
          != sa.Boolean().compile(dialect=bind.dialect)
          or existing["nullable"] is not True or existing.get("default") is not None):
        raise RuntimeError("incompatible training_plan_generation_operation.provider_attempt_possible")


def downgrade():
    bind = op.get_bind()
    retained = bind.execute(sa.text(
        f"SELECT count(*) FROM {OPERATION} WHERE {COLUMN} IS NOT NULL")).scalar()
    if retained:
        # Physical-attempt evidence decides settlement; it survives rollback.
        raise RuntimeError("physical-attempt evidence must be retained")
    with op.batch_alter_table(OPERATION) as batch:
        batch.drop_column(COLUMN)
