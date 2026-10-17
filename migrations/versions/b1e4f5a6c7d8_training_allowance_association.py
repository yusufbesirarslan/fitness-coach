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
import re

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
REF_UNIQUE = "uq_training_plan_generation_allowance_ref"
STATE_CHECK = "ck_training_plan_generation_allowance_state"
STATE_PREDICATE = "state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED', 'UNOBSERVABLE')"


def _binding_columns():
    return [
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
        sa.UniqueConstraint("reservation_ref", name=REF_UNIQUE),
        sa.CheckConstraint(STATE_PREDICATE, name=STATE_CHECK),
    ]


def _create_binding():
    op.create_table(BINDING, *_binding_columns())


# Exact PostgreSQL reflections (inspector and pg_get_constraintdef forms) of the
# fixed state predicate. Unfamiliar text fails closed; no equivalence guessing.
_PG_STATE_FORMS = (
    "state::text = ANY (ARRAY['RESERVED'::character varying, 'COMMITTED'::character varying, "
    "'RELEASED'::character varying, 'EXPIRED'::character varying, "
    "'UNOBSERVABLE'::character varying]::text[])",
    "(state)::text = ANY ((ARRAY['RESERVED'::character varying, 'COMMITTED'::character varying, "
    "'RELEASED'::character varying, 'EXPIRED'::character varying, "
    "'UNOBSERVABLE'::character varying])::text[])",
)


def _compact(text):
    parts = re.split(r"('(?:''|[^'])*')", text)
    return "".join(part if i % 2 else re.sub(r"\s+", "", part) for i, part in enumerate(parts))


def _fail(what):
    raise RuntimeError(f"training allowance binding has incompatible {what}")


def _exact(bind, column, type_, nullable):
    """Exact DDL type (VARCHAR(32) is neither CHAR(32), VARCHAR(8) nor TEXT), no server default."""
    return (column["type"].compile(dialect=bind.dialect) == type_.compile(dialect=bind.dialect)
            and column["nullable"] is nullable and column.get("default") is None)


def _verify_binding(bind):
    """A create_all-first or reentrant schema already has the table; accept only this DDL."""
    inspector = sa.inspect(bind)
    expected = [c for c in _binding_columns() if isinstance(c, sa.Column)]
    columns = {c["name"]: c for c in inspector.get_columns(BINDING)}
    if set(columns) != {c.name for c in expected} or not all(
            _exact(bind, columns[c.name], c.type, c.nullable) for c in expected):
        _fail("columns")
    if inspector.get_pk_constraint(BINDING)["constrained_columns"] != ["operation_id"]:
        _fail("primary key")
    if (REF_UNIQUE, ("reservation_ref",)) not in {
            (u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(BINDING)}:
        _fail("reference uniqueness")
    foreign = {(tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
                tuple(f["referred_columns"]), f.get("options", {}).get("ondelete"))
               for f in inspector.get_foreign_keys(BINDING)}
    if foreign != {(("operation_id",), None, OPERATION, ("id",), "CASCADE"),
                   (("user_id",), None, "user", ("id",), "CASCADE")}:
        _fail("ownership cascades")
    forms = {STATE_PREDICATE}
    if bind.dialect.name == "postgresql":
        forms |= set(_PG_STATE_FORMS)
    forms = {_compact(f) for form in forms for f in (form, f"({form})")}
    sqltext = {c["name"]: c.get("sqltext") for c in inspector.get_check_constraints(BINDING)}.get(STATE_CHECK)
    if not isinstance(sqltext, str) or _compact(sqltext) not in forms:
        _fail("state check")
    if bind.dialect.name == "postgresql":
        # NOT VALID constraints are reflected like valid ones; only the catalog knows.
        validated = dict(bind.execute(sa.text(
            "SELECT conname, convalidated FROM pg_catalog.pg_constraint "
            "WHERE conrelid = pg_catalog.to_regclass(:relation)"), {"relation": BINDING}).all())
        if validated.get(STATE_CHECK) is not True or not all(validated.values()):
            _fail("state check")
    for index in inspector.get_indexes(BINDING):
        if index["name"] == OWNER_INDEX and (
                index["column_names"] != ["user_id", "state"] or index["unique"]
                or index.get("column_sorting") or index.get("include_columns")
                or any(index.get("dialect_options", {}).values())):
            _fail("owner index")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    metered = {c["name"]: c for c in inspector.get_columns(OPERATION)}.get("allowance_metered")
    if metered is None:
        op.add_column(OPERATION, sa.Column("allowance_metered", sa.Boolean(), nullable=True))
    elif not _exact(bind, metered, sa.Boolean(), True):
        raise RuntimeError("incompatible training_plan_generation_operation.allowance_metered")
    if inspector.has_table(BINDING):
        _verify_binding(bind)
    else:
        _create_binding()
    if OWNER_INDEX not in {i["name"] for i in sa.inspect(bind).get_indexes(BINDING)}:
        op.create_index(OWNER_INDEX, BINDING, ["user_id", "state"], unique=False)

    reservation = {c["name"]: c for c in sa.inspect(bind).get_columns(RESERVATION)}
    evidence = reservation.get("billing_evidence")
    if evidence is None:
        op.add_column(RESERVATION, sa.Column("billing_evidence", sa.String(length=24), nullable=True))
    elif not _exact(bind, evidence, sa.String(length=24), True):
        raise RuntimeError("incompatible ai_usage_reservation.billing_evidence")
    billed = reservation["provider_billed"]
    if not _exact(bind, billed, sa.Boolean(), billed["nullable"]):
        raise RuntimeError("incompatible ai_usage_reservation.provider_billed")
    if not billed["nullable"]:
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
