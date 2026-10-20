"""Canonical workout-completion day claim (PRIV-K6-B1).

Expand only: a new table ``workout_completion_day`` — at most one row per
(owner, Istanbul day) (``uq_workout_completion_day``), cascading with its
owner, recording whether the completion carried a photo (``proof`` =
``photo`` | ``none``). Every NEW completion writes it, photo or photo-free.

No backfill, on purpose. Historical completions stay recognisable through
their ``PumpCheck.date_key`` (``workout_completion.queries.completed_days``
reads both). Converting rows here would have to decide which Pump Checks were
completions; a standalone Pump Check (``date_key IS NULL``) never is, and no
historical photo row is read, rewritten or re-dated by this revision.

Boot runs ``create_all`` before upgrading, so an existing table (create_all
first, or a re-entered upgrade) is accepted only if its DDL is exactly this
one; anything else fails closed. Downgrade refuses while any claim exists:
dropping it would silently un-complete photo-free workout days.
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "f9c6d0a1b2e3"
down_revision = "e7a1c3d5f9b2"
branch_labels = None
depends_on = None
expand_contract = "expand"

TABLE = "workout_completion_day"
DAY_UNIQUE = "uq_workout_completion_day"
PROOF_CHECK = "ck_workout_completion_day_proof"
PROOF_PREDICATE = "proof IN ('photo', 'none')"


def _claim_columns():
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date_key", sa.String(length=10), nullable=False),
        sa.Column("proof", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date_key", name=DAY_UNIQUE),
        sa.CheckConstraint(PROOF_PREDICATE, name=PROOF_CHECK),
    ]


def _create_claim():
    op.create_table(TABLE, *_claim_columns())


# Exact PostgreSQL reflections (inspector and pg_get_constraintdef forms) of the
# fixed proof predicate. Unfamiliar text fails closed; no equivalence guessing.
_PG_PROOF_FORMS = (
    "proof::text = ANY (ARRAY['photo'::character varying, 'none'::character varying]::text[])",
    "(proof)::text = ANY ((ARRAY['photo'::character varying, "
    "'none'::character varying])::text[])",
)


def _compact(text):
    parts = re.split(r"('(?:''|[^'])*')", text)
    return "".join(part if i % 2 else re.sub(r"\s+", "", part) for i, part in enumerate(parts))


def _fail(what):
    raise RuntimeError(f"workout completion day claim has incompatible {what}")


def _same_type(bind, column, type_, nullable):
    """Exact DDL type (VARCHAR(10) is neither CHAR(10), VARCHAR(8) nor TEXT)."""
    return (column["type"].compile(dialect=bind.dialect) == type_.compile(dialect=bind.dialect)
            and column["nullable"] is nullable)


def _identity_default(bind, column):
    """The surrogate key may only carry its own sequence default (PostgreSQL)."""
    default = column.get("default")
    if bind.dialect.name == "sqlite":
        # SQLite INTEGER PRIMARY KEY generates rowids without a default.
        return default is None
    return (bind.dialect.name == "postgresql"
            and default == f"nextval('{TABLE}_id_seq'::regclass)")


def _verify_sequence(bind):
    """Verify SERIAL catalog authority, without consuming or resetting a value.

    The reflected default is checked separately. Its regclass dependency must
    resolve to the same sequence that is auto-owned by this exact ID column.
    Table and sequence must be permanent: crash recovery resets UNLOGGED ones.
    Check nextval privileges for the verifying role only. Runtime counters and
    grants to a different application role remain separate qualification.
    """
    sequence = bind.execute(sa.text("""
        SELECT s.seqtypid = 'pg_catalog.int4'::pg_catalog.regtype AS integer_type,
               s.seqincrement, s.seqmin, s.seqmax, s.seqcycle,
               q.relnamespace = t.relnamespace AS same_namespace,
               q.relowner = t.relowner AS same_owner,
               a.attidentity = '' AS serial_column,
               t.relpersistence = 'p' AND q.relpersistence = 'p' AS crash_safe,
               pg_catalog.pg_get_serial_sequence(:relation, 'id')::pg_catalog.regclass
                   = q.oid AS associated_sequence,
               (pg_catalog.has_sequence_privilege(q.oid, 'USAGE') OR
                pg_catalog.has_sequence_privilege(q.oid, 'UPDATE')) AS usable_by_current_role,
               NOT EXISTS (
                   SELECT 1 FROM pg_catalog.pg_depend custom_function
                   WHERE custom_function.classid = 'pg_catalog.pg_attrdef'::pg_catalog.regclass
                     AND custom_function.objid = d.oid
                     AND custom_function.refclassid = 'pg_catalog.pg_proc'::pg_catalog.regclass
               ) AS builtin_default
        FROM pg_catalog.pg_class t
        JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
             AND a.attname = 'id' AND NOT a.attisdropped
        JOIN pg_catalog.pg_attrdef d ON d.adrelid = t.oid AND d.adnum = a.attnum
        JOIN pg_catalog.pg_depend dep ON dep.classid = 'pg_catalog.pg_attrdef'::pg_catalog.regclass
             AND dep.objid = d.oid AND dep.objsubid = 0
             AND dep.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
             AND dep.refobjsubid = 0 AND dep.deptype = 'n'
        JOIN pg_catalog.pg_class q ON q.oid = dep.refobjid AND q.relkind = 'S'
        JOIN pg_catalog.pg_sequence s ON s.seqrelid = q.oid
        JOIN pg_catalog.pg_depend owned ON owned.classid = 'pg_catalog.pg_class'::pg_catalog.regclass
             AND owned.objid = q.oid AND owned.objsubid = 0
             AND owned.refclassid = 'pg_catalog.pg_class'::pg_catalog.regclass
             AND owned.refobjid = t.oid AND owned.refobjsubid = a.attnum
             AND owned.deptype = 'a'
        WHERE t.oid = pg_catalog.to_regclass(:relation)
          AND q.relname = :sequence
    """), {"relation": TABLE, "sequence": f"{TABLE}_id_seq"}).all()
    if len(sequence) != 1 or tuple(sequence[0]) != (
            True, 1, 1, 2147483647, False, True, True, True, True, True, True, True):
        _fail("id sequence")


def _verify_claim(bind):
    """A create_all-first or reentrant schema already has the table; accept only this DDL."""
    inspector = sa.inspect(bind)
    expected = [c for c in _claim_columns() if isinstance(c, sa.Column)]
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    if set(columns) != {c.name for c in expected} or not all(
            _same_type(bind, columns[c.name], c.type, c.nullable) for c in expected):
        _fail("columns")
    if not _identity_default(bind, columns["id"]) or any(
            columns[c.name].get("default") is not None for c in expected if c.name != "id"):
        _fail("column defaults")
    if bind.dialect.name == "postgresql":
        _verify_sequence(bind)
    if inspector.get_pk_constraint(TABLE)["constrained_columns"] != ["id"]:
        _fail("primary key")
    if {(DAY_UNIQUE, ("user_id", "date_key"))} != {
            (u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(TABLE)}:
        _fail("day uniqueness")
    # PostgreSQL reflects constraint-backed indexes too. Every other unique
    # index (including partial/expression indexes) is additional write policy.
    if any(i.get("unique") and i.get("duplicates_constraint") != DAY_UNIQUE
           for i in inspector.get_indexes(TABLE)):
        _fail("unique indexes")
    foreign = {(tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
                tuple(f["referred_columns"]), f.get("options", {}).get("ondelete"))
               for f in inspector.get_foreign_keys(TABLE)}
    if foreign != {(("user_id",), None, "user", ("id",), "CASCADE")}:
        _fail("ownership cascade")
    forms = {PROOF_PREDICATE}
    if bind.dialect.name == "postgresql":
        forms |= set(_PG_PROOF_FORMS)
    forms = {_compact(f) for form in forms for f in (form, f"({form})")}
    checks = inspector.get_check_constraints(TABLE)
    if len(checks) != 1 or checks[0]["name"] != PROOF_CHECK:
        _fail("proof check")
    sqltext = checks[0].get("sqltext")
    if not isinstance(sqltext, str) or _compact(sqltext) not in forms:
        _fail("proof check")
    if bind.dialect.name == "postgresql":
        # NOT VALID constraints are reflected like valid ones; only the catalog knows.
        validated = dict(bind.execute(sa.text(
            "SELECT conname, convalidated FROM pg_catalog.pg_constraint "
            "WHERE conrelid = pg_catalog.to_regclass(:relation)"), {"relation": TABLE}).all())
        if validated.get(PROOF_CHECK) is not True or not all(validated.values()):
            _fail("proof check")


def upgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table(TABLE):
        _verify_claim(bind)
    else:
        _create_claim()


def downgrade():
    bind = op.get_bind()
    retained = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    if retained:
        # Completed workout days survive rollback; dropping them would let a
        # photo-free day be completed (and rewarded) a second time.
        raise RuntimeError("workout completion day claims must be retained")
    op.drop_table(TABLE)
