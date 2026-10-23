"""Add empty Apple notification receipt and reconciliation request tables (MON-02-BE-02).

Expand only. No User, subscription, ledger or historical row is read or changed.
Reentrant for create_all-first boot: an existing table is accepted only with
exactly this contract, otherwise the upgrade fails closed and changes nothing.
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "a4d6f8b0c2e1"
down_revision = "d7e3a9c1f5b8"
branch_labels = None
depends_on = None
expand_contract = "expand"

RECEIPT = "apple_notification_receipt"
REQUEST = "apple_subscription_reconciliation"
TZ = sa.DateTime(timezone=True)


def _receipt_columns():
    return [
        sa.Column("notification_uuid", sa.String(36), primary_key=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("notification_type", sa.String(40), nullable=False),
        sa.Column("subtype", sa.String(40)),
        sa.Column("original_transaction_id", sa.String(64)),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("signed_at", TZ, nullable=False),
        sa.Column("received_at", TZ, nullable=False),
    ]


def _request_columns():
    return [
        sa.Column("environment", sa.String(10), primary_key=True),
        sa.Column("original_transaction_id", sa.String(64), primary_key=True),
        sa.Column("account_token", sa.String(36)),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TZ, nullable=False),
        sa.Column("lease_until", TZ),
        sa.Column("last_outcome", sa.String(40)),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
    ]


def _contracts():
    return (
        (RECEIPT, _receipt_columns, ["notification_uuid"], "ck_apple_notification_environment", None),
        (REQUEST, _request_columns, ["environment", "original_transaction_id"],
         "ck_apple_reconciliation_environment",
         ("ix_apple_reconciliation_due", ["environment", "next_attempt_at"])),
    )


def _environment_check(name, sqltext, dialect):
    """Accept only the fixed IN predicate and its PostgreSQL deparse forms."""
    def compact(text):
        parts = re.split(r"('(?:''|[^'])*')", text)
        return "".join(part if i % 2 else re.sub(r"\s+", "", part) for i, part in enumerate(parts))
    forms = {"environment IN ('Sandbox', 'Production')"}
    if dialect == "postgresql":
        forms.add("environment::text = ANY (ARRAY['Sandbox'::character varying, "
                  "'Production'::character varying]::text[])")
        forms.add("(environment)::text = ANY ((ARRAY['Sandbox'::character varying, "
                  "'Production'::character varying])::text[])")
    forms |= {f"({form})" for form in forms}
    return isinstance(sqltext, str) and compact(sqltext) in {compact(form) for form in forms}


def _verify(bind, table, columns, primary, check, index):
    inspector = sa.inspect(bind)
    actual = {c["name"]: c for c in inspector.get_columns(table)}
    expected = columns()
    valid = set(actual) == {c.name for c in expected} and all(
        actual[c.name]["type"].compile(dialect=bind.dialect) == c.type.compile(dialect=bind.dialect)
        and actual[c.name]["nullable"] is (c.nullable and not c.primary_key)
        and (bind.dialect.name != "postgresql" or not isinstance(c.type, sa.DateTime)
             or actual[c.name]["type"].timezone is True)
        for c in expected)
    valid = valid and inspector.get_pk_constraint(table)["constrained_columns"] == primary
    valid = valid and not inspector.get_foreign_keys(table) and not inspector.get_unique_constraints(table)
    checks = {c["name"]: c.get("sqltext") for c in inspector.get_check_constraints(table)}
    valid = valid and set(checks) == {check} and _environment_check(check, checks[check], bind.dialect.name)
    indexes = [(i["name"], i["column_names"], bool(i["unique"])) for i in inspector.get_indexes(table)]
    valid = valid and indexes == ([(index[0], index[1], False)] if index else [])
    if valid and bind.dialect.name == "postgresql":
        states = bind.execute(sa.text(
            "SELECT contype, convalidated, pg_catalog.pg_get_constraintdef(oid) "
            "FROM pg_catalog.pg_constraint "
            "WHERE conrelid = pg_catalog.to_regclass(:relation)"),
            {"relation": bind.dialect.identifier_preparer.format_table(sa.table(table))}).all()
        # PG18 catalogs NOT NULL constraints; PG16 exposes only column
        # nullability (already checked above). Normalize only the complete,
        # validated set for the required columns, independent of names.
        not_null = [(v, text) for t, v, text in states if t == "n"]
        required = [(True, f"NOT NULL {c.name}") for c in expected
                    if not c.nullable or c.primary_key]
        valid = (not not_null or sorted(not_null) == sorted(required))
        valid = valid and sorted(t for t, _, _ in states if t != "n") == ["c", "p"]
        valid = valid and all(v is True for _, v, _ in states)
    if not valid:
        raise RuntimeError("incompatible Apple reconciliation schema")


def upgrade():
    bind = op.get_bind()
    # Verify every pre-existing table first: incompatible adoption changes nothing.
    existing = [c for c in _contracts() if sa.inspect(bind).has_table(c[0])]
    for contract in existing:
        _verify(bind, *contract)
    for table, columns, _primary, check, index in _contracts():
        if any(c[0] == table for c in existing):
            continue
        op.create_table(table, *columns(),
                        sa.CheckConstraint("environment IN ('Sandbox', 'Production')", name=check))
        if index:
            op.create_index(index[0], table, index[1], unique=False)


def downgrade():
    # Erases notification receipts and pending requests; not an operational rollback.
    op.drop_index("ix_apple_reconciliation_due", table_name=REQUEST)
    op.drop_table(REQUEST)
    op.drop_table(RECEIPT)
