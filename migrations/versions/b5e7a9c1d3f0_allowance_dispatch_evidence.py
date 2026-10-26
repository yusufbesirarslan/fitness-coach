"""Add generic dispatch evidence to the existing allowance reservation.

Historical rows and native Training keep their original authority. Downgrade
deliberately retains financial evidence. Reapplying validates column shapes.
"""
from alembic import op
import sqlalchemy as sa

revision = "b5e7a9c1d3f0"
down_revision = "a4d6f8b0c2e1"
branch_labels = None
depends_on = None
expand_contract = "expand"


def _exists(columns, name, affinity, length=None):
    existing = columns.get(name)
    if existing is None:
        return False
    if (not existing["nullable"] or existing["type"]._type_affinity is not affinity
            or (length is not None and existing["type"].length != length)
            or existing.get("default") is not None or existing.get("computed")
            or existing.get("identity")):
        raise RuntimeError("allowance dispatch evidence schema mismatch")
    return True


def upgrade():
    columns = {c["name"]: c for c in sa.inspect(op.get_bind()).get_columns("ai_usage_reservation")}
    if not _exists(columns, "request_fingerprint", sa.String, 64):
        op.add_column("ai_usage_reservation", sa.Column("request_fingerprint", sa.String(64), nullable=True))
    if not _exists(columns, "physical_attempts", sa.Integer):
        op.add_column("ai_usage_reservation", sa.Column("physical_attempts", sa.Integer(), nullable=True))
    if not _exists(columns, "physical_attempt_limit", sa.Integer):
        op.add_column("ai_usage_reservation", sa.Column("physical_attempt_limit", sa.Integer(), nullable=True))
    if not _exists(columns, "canonical_committed", sa.Boolean):
        op.add_column("ai_usage_reservation", sa.Column("canonical_committed", sa.Boolean(), nullable=True))


def downgrade():
    # Forward-only accounting evidence: rollback code, never erase receipts.
    pass
