"""AXISAI-LAUNCH-FREE-BE-01: allow the LAUNCH_FREE allowance bucket scope.

Expand only: widens ``ck_ai_allowance_bucket_scope`` from
``STARTER | PREMIUM`` to ``STARTER | PREMIUM | LAUNCH_FREE``. No row is written,
rewritten or deleted, and every other constraint is untouched. Code from before
this revision only writes STARTER/PREMIUM, so the previous release stays
compatible with the widened schema (R6 expand-first).

Verify-or-replace, fail closed: an already widened, validated predicate is
accepted as is; the exact original predicate is replaced (drop + add in this
one transaction); anything else, including NOT VALID, stops the migration.
Downgrade restores the original predicate only while no LAUNCH_FREE bucket
exists.

Shipped ``a9c1d2e3f4b5`` is immutable and pins the original predicate, so a
fresh create_all-first boot presents it first (``app.db_init.
replay_shipped_predicates``) and this revision widens it, exactly as on a
migrated database. The static expand gate cannot prove a CHECK replacement
overlap-safe; this file is pinned in
``scripts/migration_expand_contract.py::REVIEWED_DDL`` after line-by-line
review: the serving revision never writes LAUNCH_FREE, so widening changes
nothing it does.
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "d8f1a3c5e7b9"
down_revision = "c6f8b0d2e4a1"
branch_labels = None
depends_on = None
expand_contract = "expand"

TABLE = "ai_allowance_bucket"
NAME = "ck_ai_allowance_bucket_scope"
NARROW = "scope IN ('STARTER', 'PREMIUM')"
WIDE = "scope IN ('STARTER', 'PREMIUM', 'LAUNCH_FREE')"


def _pg_forms(values):
    array = ", ".join(f"'{v}'::character varying" for v in values)
    return (f"scope::text = ANY (ARRAY[{array}]::text[])",
            f"(scope)::text = ANY ((ARRAY[{array}])::text[])")


_FORMS = {
    NARROW: (NARROW, *_pg_forms(("STARTER", "PREMIUM"))),
    WIDE: (WIDE, *_pg_forms(("STARTER", "PREMIUM", "LAUNCH_FREE"))),
}


def _compact(text):
    parts = re.split(r"('(?:''|[^'])*')", text)
    return "".join(part if i % 2 else re.sub(r"\s+", "", part) for i, part in enumerate(parts))


def _is(sqltext, predicate, dialect):
    forms = _FORMS[predicate] if dialect == "postgresql" else _FORMS[predicate][:1]
    forms = set(forms) | {f"({form})" for form in forms}
    return isinstance(sqltext, str) and _compact(sqltext) in {_compact(f) for f in forms}


def _current(bind):
    checks = {c["name"]: c.get("sqltext") for c in sa.inspect(bind).get_check_constraints(TABLE)}
    if NAME not in checks:
        raise RuntimeError("AI allowance bucket scope constraint is missing")
    sqltext = checks[NAME]
    if bind.dialect.name == "postgresql":
        valid = bind.execute(sa.text(
            "SELECT convalidated FROM pg_catalog.pg_constraint "
            "WHERE conrelid = pg_catalog.to_regclass(:relation) AND conname = :name"),
            {"relation": bind.dialect.identifier_preparer.quote(TABLE), "name": NAME}).scalar()
        if valid is not True:
            raise RuntimeError("AI allowance bucket scope constraint is not validated")
    if _is(sqltext, WIDE, bind.dialect.name):
        return WIDE
    if _is(sqltext, NARROW, bind.dialect.name):
        return NARROW
    raise RuntimeError("AI allowance bucket scope constraint has an unfamiliar predicate")


def _replace(predicate):
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(NAME, type_="check")
        batch.create_check_constraint(NAME, predicate)


def upgrade():
    bind = op.get_bind()
    if _current(bind) == NARROW:
        _replace(WIDE)
    if _current(bind) != WIDE:
        raise RuntimeError("AI allowance bucket scope constraint did not widen")


def downgrade():
    # Never strand or erase campaign balances: rollback is the previous code
    # on this widened schema. Narrowing is possible only while none exist.
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {TABLE} WHERE scope = 'LAUNCH_FREE' LIMIT 1")).first():
        raise RuntimeError("LAUNCH_FREE scope downgrade refuses populated campaign buckets")
    if _current(bind) == WIDE:
        _replace(NARROW)
