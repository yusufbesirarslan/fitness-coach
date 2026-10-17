"""MON-02-BE-03A: empty AI allowance ledger tables; no backfill, no grants.

Adds ``ai_starter_grant``, ``ai_allowance_bucket`` and ``ai_usage_reservation``
(all owner rows ``ON DELETE CASCADE``). Touches no existing table and writes no
row: existing accounts receive nothing from this migration.

Boot runs ``create_all`` before upgrading, so an existing table is accepted only
when it already carries the complete safe schema (same verify-or-create stance
as ``c2b9d0e1f2a3``); anything else fails closed.
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "a9c1d2e3f4b5"
down_revision = "c2b9d0e1f2a3"
branch_labels = None
depends_on = None
expand_contract = "expand"

_TZ = sa.DateTime(timezone=True)


def _grant_columns():
    return [
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("granted_at", _TZ, nullable=False),
    ]


def _bucket_columns():
    return [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("period_key", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("granted", sa.Integer, nullable=False),
        sa.Column("used", sa.Integer, nullable=False),
        sa.Column("reserved", sa.Integer, nullable=False),
        sa.Column("billed_releases", sa.Integer, nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.UniqueConstraint("user_id", "scope", "feature", "period_key", name="uq_ai_allowance_bucket"),
        sa.CheckConstraint("scope IN ('STARTER', 'PREMIUM')", name="ck_ai_allowance_bucket_scope"),
        sa.CheckConstraint("granted >= 0", name="ck_ai_allowance_bucket_granted"),
        sa.CheckConstraint("used >= 0", name="ck_ai_allowance_bucket_used"),
        sa.CheckConstraint("reserved >= 0", name="ck_ai_allowance_bucket_reserved"),
        sa.CheckConstraint("billed_releases >= 0", name="ck_ai_allowance_bucket_billed"),
        sa.CheckConstraint("used + reserved <= granted", name="ck_ai_allowance_bucket_within"),
    ]


def _reservation_columns():
    return [
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bucket_id", sa.Integer, sa.ForeignKey("ai_allowance_bucket.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("feature", sa.String(32), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(32)),
        sa.Column("provider_billed", sa.Boolean, nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("expires_at", _TZ, nullable=False),
        sa.Column("finalized_at", _TZ),
        sa.UniqueConstraint("public_id", name="uq_ai_usage_reservation_public"),
        sa.UniqueConstraint("user_id", "key_digest", name="uq_ai_usage_reservation_key"),
        sa.CheckConstraint("state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED')",
                           name="ck_ai_usage_reservation_state"),
    ]


_INDEXES = {"ai_usage_reservation": ("ix_ai_usage_reservation_open", ["user_id", "state", "expires_at"])}
_FOREIGN = {
    "ai_starter_grant": {("user_id", "user")},
    "ai_allowance_bucket": {("user_id", "user")},
    "ai_usage_reservation": {("user_id", "user"), ("bucket_id", "ai_allowance_bucket")},
}
# Exact PostgreSQL reflections (inspector and pg_get_constraintdef forms) of the
# fixed predicates above. Unfamiliar text fails closed; no equivalence guessing.
_PG_FORMS = {
    "ck_ai_allowance_bucket_scope": (
        "scope::text = ANY (ARRAY['STARTER'::character varying, 'PREMIUM'::character varying]::text[])",
        "(scope)::text = ANY ((ARRAY['STARTER'::character varying, 'PREMIUM'::character varying])::text[])"),
    "ck_ai_allowance_bucket_within": ("(used + reserved) <= granted",),
    "ck_ai_usage_reservation_state": (
        "state::text = ANY (ARRAY['RESERVED'::character varying, 'COMMITTED'::character varying, "
        "'RELEASED'::character varying, 'EXPIRED'::character varying]::text[])",
        "(state)::text = ANY ((ARRAY['RESERVED'::character varying, 'COMMITTED'::character varying, "
        "'RELEASED'::character varying, 'EXPIRED'::character varying])::text[])"),
}


def _tables():
    metadata = sa.MetaData()
    sa.Table("user", metadata, sa.Column("id", sa.Integer, primary_key=True))
    return [sa.Table("ai_starter_grant", metadata, *_grant_columns()),
            sa.Table("ai_allowance_bucket", metadata, *_bucket_columns()),
            sa.Table("ai_usage_reservation", metadata, *_reservation_columns())]


def _create(table):
    if table.name == "ai_starter_grant":
        columns = _grant_columns()
    elif table.name == "ai_allowance_bucket":
        columns = _bucket_columns()
    else:
        columns = _reservation_columns()
    op.create_table(table.name, *columns)
    if table.name in _INDEXES:
        name, columns = _INDEXES[table.name]
        op.create_index(name, table.name, columns, unique=False)


def _compact(text):
    parts = re.split(r"('(?:''|[^'])*')", text)
    return "".join(part if i % 2 else re.sub(r"\s+", "", part) for i, part in enumerate(parts))


def _check_matches(sqltext, expected, dialect):
    forms = {str(expected.sqltext)}
    if dialect == "postgresql":
        forms |= set(_PG_FORMS.get(expected.name, ()))
    forms |= {f"({form})" for form in forms}
    return isinstance(sqltext, str) and _compact(sqltext) in {_compact(f) for f in forms}


def _fail(what):
    raise RuntimeError(f"AI allowance ledger table has incompatible {what}")


def _verify(bind, table):
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns(table.name)}
    if set(columns) != set(table.c.keys()):
        _fail("columns")
    for column in table.c:
        actual = columns[column.name]
        if (actual["nullable"] != column.nullable
                or actual["type"]._type_affinity != column.type._type_affinity
                or (isinstance(column.type, sa.String) and actual["type"].length != column.type.length)
                or (bind.dialect.name == "postgresql" and isinstance(column.type, sa.DateTime)
                    and actual["type"].timezone is not True)):
            _fail("types")
    if inspector.get_pk_constraint(table.name)["constrained_columns"] != [c.name for c in table.primary_key]:
        _fail("primary key")
    unique = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name)}
    required = {tuple(c.name for c in u.columns) for u in table.constraints if isinstance(u, sa.UniqueConstraint)}
    if not required <= unique:
        _fail("unique constraints")
    foreign = {(f["constrained_columns"][0], f["referred_table"]) for f in inspector.get_foreign_keys(table.name)
               if len(f["constrained_columns"]) == 1 and f["referred_columns"] == ["id"]
               and f.get("options", {}).get("ondelete") == "CASCADE"}
    if not _FOREIGN[table.name] <= foreign:
        _fail("ownership constraints")
    checks = {c["name"]: c.get("sqltext") for c in inspector.get_check_constraints(table.name)}
    required_checks = [c for c in table.constraints if isinstance(c, sa.CheckConstraint)]
    if any(not _check_matches(checks.get(c.name), c, bind.dialect.name) for c in required_checks):
        _fail("check constraints")
    if bind.dialect.name == "postgresql" and required_checks:
        relation = bind.dialect.identifier_preparer.format_table(table)
        try:
            states = dict(bind.execute(sa.text(
                "SELECT conname, convalidated FROM pg_catalog.pg_constraint "
                "WHERE conrelid = pg_catalog.to_regclass(:relation) AND contype = 'c'"
            ), {"relation": relation}).all())
        except sa.exc.SQLAlchemyError:
            _fail("check constraints")
        if any(states.get(c.name) is not True for c in required_checks):
            _fail("check constraints")
    if table.name in _INDEXES:
        name, cols = _INDEXES[table.name]
        if not any(i["name"] == name and i["column_names"] == cols for i in inspector.get_indexes(table.name)):
            _fail("indexes")


def upgrade():
    bind = op.get_bind()
    for table in _tables():
        if sa.inspect(bind).has_table(table.name):
            _verify(bind, table)
        else:
            _create(table)


def downgrade():
    # Runtime rollback is flag-OFF. Never erase granted balances or history.
    bind = op.get_bind()
    for table in _tables():
        if sa.inspect(bind).has_table(table.name):
            if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
                raise RuntimeError("AI allowance ledger downgrade refuses populated tables")
    op.drop_table("ai_usage_reservation")
    op.drop_table("ai_allowance_bucket")
    op.drop_table("ai_starter_grant")
