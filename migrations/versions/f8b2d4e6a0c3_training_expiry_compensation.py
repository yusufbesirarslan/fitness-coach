"""Separate exactly-once receipt for proven zero-attempt Training expiry.

Additive only; historical UNKNOWN rows are neither backfilled nor reclassified.
"""
from alembic import op
import sqlalchemy as sa

revision = "f8b2d4e6a0c3"
down_revision = "e7a1c3d5f9b2"
branch_labels = None
depends_on = None
expand_contract = "expand"
TABLE = "training_allowance_compensation"


def _columns():
    return [
        sa.Column("reservation_id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("bucket_id", sa.Integer(), nullable=False),
        sa.Column("proof_version", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _foreign():
    return {"reservation_id": "ai_usage_reservation", "operation_id": "training_plan_generation_operation",
            "user_id": "user", "bucket_id": "ai_allowance_bucket"}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        op.create_table(TABLE, *_columns(), sa.PrimaryKeyConstraint("reservation_id"),
            *[sa.ForeignKeyConstraint([col], [target + ".id"], ondelete="CASCADE")
              for col, target in _foreign().items()],
            sa.UniqueConstraint("operation_id", name="uq_training_compensation_operation"),
            sa.CheckConstraint("proof_version = 'TRACKED_TRAINING_V1'", name="ck_training_compensation_proof"),
            sa.CheckConstraint("reason = 'EXPIRED_ZERO_ATTEMPTS'", name="ck_training_compensation_reason"))
        return
    # create_all-first / reentry accepts exactly the deployed table contract.
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    expected = _columns()
    valid = set(columns) == {c.name for c in expected} and all(
        columns[c.name]["type"].compile(dialect=bind.dialect) == c.type.compile(dialect=bind.dialect)
        and columns[c.name]["nullable"] is False and columns[c.name].get("default") is None
        for c in expected)
    valid = valid and inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["reservation_id"]
    valid = valid and ("operation_id",) in {
        tuple(u["column_names"]) for u in inspector.get_unique_constraints(TABLE)}
    foreign = {(tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]),
                f.get("options", {}).get("ondelete")) for f in inspector.get_foreign_keys(TABLE)}
    valid = valid and foreign == {((col,), target, ("id",), "CASCADE") for col, target in _foreign().items()}
    checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(TABLE)}
    # PostgreSQL reflection inserts casts and parentheses; require exact tokens.
    import re
    def compact(value):
        return re.sub(r"[\s()]|::text|::character varying", "", value)
    valid = valid and {k: compact(v) for k, v in checks.items()} == {
        "ck_training_compensation_proof": "proof_version='TRACKED_TRAINING_V1'",
        "ck_training_compensation_reason": "reason='EXPIRED_ZERO_ATTEMPTS'"}
    if not valid:
        raise RuntimeError("incompatible training expiry compensation schema")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM " + TABLE + ")")).scalar():
        raise RuntimeError("committed compensation evidence must be retained")
    op.drop_table(TABLE)
