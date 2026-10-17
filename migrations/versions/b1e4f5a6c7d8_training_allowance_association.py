"""Native Training allowance binding and tri-state billing evidence.

MON-02-BE-03B-01B. Expand only:

* ``training_plan_generation_operation.allowance_metered`` (nullable): the
  metering mode fixed at claim; NULL = unmetered/historical;
* new table ``training_plan_generation_allowance`` — at most one allowance
  hold per generation operation (PK), at most one operation per ledger hold
  (UNIQUE), cascading with its operation and owner;
* ``ai_usage_reservation.billing_evidence`` (nullable) and ``provider_billed``
  relaxed to NULL so UNKNOWN is never stored as False or True.

No backfill: historical operations stay unmetered (NULL, no binding) and BE-03A
holds keep their Boolean with NULL evidence.
"""
from alembic import op
import sqlalchemy as sa

revision = "b1e4f5a6c7d8"
down_revision = "b0d3e4f5a6b7"
branch_labels = None
depends_on = None
expand_contract = "expand"

OPERATION = "training_plan_generation_operation"
BINDING = "training_plan_generation_allowance"
RESERVATION = "ai_usage_reservation"
OWNER_INDEX = "ix_training_plan_generation_allowance_owner"


def _create_binding():
    op.create_table(
        BINDING,
        sa.Column("operation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reservation_ref", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["training_plan_generation_operation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("reservation_ref", name="uq_training_plan_generation_allowance_ref"),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED', 'UNOBSERVABLE')",
            name="ck_training_plan_generation_allowance_state"),
    )


def _verify_binding(inspector):
    """A create_all-first schema already has the table; refuse an incompatible one."""
    columns = {c["name"]: c for c in inspector.get_columns(BINDING)}
    expected = {"operation_id": False, "user_id": False, "reservation_ref": False,
                "state": False, "created_at": False, "updated_at": False}
    if set(columns) != set(expected) or any(
            columns[name]["nullable"] != nullable for name, nullable in expected.items()):
        raise RuntimeError("incompatible training allowance binding columns")
    if inspector.get_pk_constraint(BINDING)["constrained_columns"] != ["operation_id"]:
        raise RuntimeError("incompatible training allowance binding key")
    if ("reservation_ref",) not in {
            tuple(u["column_names"]) for u in inspector.get_unique_constraints(BINDING)}:
        raise RuntimeError("training allowance binding is missing its reference uniqueness")
    cascades = {(f["constrained_columns"][0], f["referred_table"])
                for f in inspector.get_foreign_keys(BINDING)
                if f.get("options", {}).get("ondelete") == "CASCADE"}
    if not {("operation_id", "training_plan_generation_operation"), ("user_id", "user")} <= cascades:
        raise RuntimeError("training allowance binding is missing its cascades")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    metered = {c["name"]: c for c in inspector.get_columns(OPERATION)}.get("allowance_metered")
    if metered is None:
        op.add_column(OPERATION, sa.Column("allowance_metered", sa.Boolean(), nullable=True))
    elif not isinstance(metered["type"], sa.Boolean) or not metered["nullable"]:
        raise RuntimeError("incompatible training_plan_generation_operation.allowance_metered")
    if inspector.has_table(BINDING):
        _verify_binding(inspector)
    else:
        _create_binding()
    if OWNER_INDEX not in {i["name"] for i in sa.inspect(bind).get_indexes(BINDING)}:
        op.create_index(OWNER_INDEX, BINDING, ["user_id", "state"], unique=False)

    reservation = {c["name"]: c for c in sa.inspect(bind).get_columns(RESERVATION)}
    evidence = reservation.get("billing_evidence")
    if evidence is None:
        op.add_column(RESERVATION, sa.Column("billing_evidence", sa.String(length=24), nullable=True))
    elif (not isinstance(evidence["type"], sa.String) or evidence["type"].length != 24
          or not evidence["nullable"]):
        raise RuntimeError("incompatible ai_usage_reservation.billing_evidence")
    if not reservation["provider_billed"]["nullable"]:
        with op.batch_alter_table(RESERVATION) as batch:
            batch.alter_column("provider_billed", existing_type=sa.Boolean(), nullable=True)


def downgrade():
    bind = op.get_bind()
    retained = bind.execute(sa.text(f"SELECT count(*) FROM {BINDING}")).scalar()
    retained += bind.execute(sa.text(
        f"SELECT count(*) FROM {OPERATION} WHERE allowance_metered IS NOT NULL")).scalar()
    retained += bind.execute(sa.text(
        f"SELECT count(*) FROM {RESERVATION} "
        "WHERE billing_evidence IS NOT NULL OR provider_billed IS NULL")).scalar()
    if retained:
        # Metering associations and UNKNOWN billing evidence survive rollback.
        raise RuntimeError("allowance bindings and billing evidence must be retained")
    with op.batch_alter_table(RESERVATION) as batch:
        batch.alter_column("provider_billed", existing_type=sa.Boolean(), nullable=False)
        batch.drop_column("billing_evidence")
    op.drop_table(BINDING)
    with op.batch_alter_table(OPERATION) as batch:
        batch.drop_column("allowance_metered")
