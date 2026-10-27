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


def _fixed_check(expression, column, literal):
    # Only equality of the named column and exact literal is equivalent here.
    # PostgreSQL adds grouping and text/varchar casts. Tokenize BEFORE removing
    # formatting, so whitespace/parentheses inside a literal stay significant.
    import re
    tokens = re.findall(r"'(?:(?:'')|[^'])*'|\"[^\"]*\"|::|[A-Za-z_][A-Za-z_0-9]*|[^\s]", expression)
    tokens = [t for t in tokens if t not in ("(", ")")]
    def operand(parts, expected):
        if not parts or parts[0] not in expected:
            return False
        rest = parts[1:]
        while rest:
            if rest[:2] == ["::", "text"]:
                rest = rest[2:]
            elif rest[:3] == ["::", "character", "varying"]:
                rest = rest[3:]
            else:
                return False
        return True
    if tokens.count("=") != 1:
        return False
    equal = tokens.index("=")
    return (operand(tokens[:equal], (column, '"' + column + '"'))
            and operand(tokens[equal + 1:], ("'" + literal + "'",)))


def _postgres_contract(bind, table=TABLE, kinds=("c", "c", "f", "f", "f", "f", "p", "u"), unique=("p", "u"),
                       not_null=("reservation_id", "operation_id", "user_id", "bucket_id",
                                 "proof_version", "reason", "applied_at")):
    # Reflection omits validation status and can hide standalone unique indexes.
    # Inspect the effective authority, without validating/repairing any evidence.
    pg18 = int(bind.exec_driver_sql("SELECT current_setting('server_version_num')").scalar_one()) >= 180000
    # conenforced was added in PG18. PG16 constraints are always enforced.
    constraints = bind.execute(sa.text("""
        SELECT c.oid, c.contype, c.convalidated, c.condeferrable, c.condeferred,
               c.connoinherit, c.confupdtype, c.confmatchtype,
               CASE WHEN current_setting('server_version_num')::int >= 180000
                    THEN (to_jsonb(c)->>'conenforced')::boolean ELSE true END AS enforced,
               c.conkey, c.conislocal, c.coninhcount,
               c.conparentid, c.contypid, c.conindid, c.confrelid,
               c.conbin IS NULL AS no_expression,
               c.connamespace = (SELECT relnamespace FROM pg_class
                                WHERE oid = c.conrelid) AS same_namespace
        FROM pg_constraint c WHERE c.conrelid = to_regclass(:table)
    """), {"table": table}).mappings().all()
    attributes = bind.execute(sa.text("""
        SELECT attnum, attname, attnotnull, attislocal, attinhcount
        FROM pg_attribute WHERE attrelid = to_regclass(:table)
          AND attnum > 0 AND NOT attisdropped
    """), {"table": table}).mappings().all()
    if (set(a["attname"] for a in attributes) != set(not_null)
            or any(not a["attnotnull"] or not a["attislocal"] or a["attinhcount"]
                   for a in attributes)):
        return False
    # PG18 makes each NOT NULL authority explicit. Require a bijection with
    # the expected live columns, not merely attnotnull (which can be invalid).
    # Constraint names may differ harmlessly; their column authority may not.
    nulls = [c for c in constraints if c["contype"] == "n"]
    expected_keys = sorted((a["attnum"],) for a in attributes) if pg18 else []
    if (any(not c["conkey"] or len(c["conkey"]) != 1 or c["connoinherit"]
            or not c["conislocal"] or c["coninhcount"] or c["conparentid"]
            or c["contypid"] or c["conindid"] or c["confrelid"]
            or not c["no_expression"] or not c["same_namespace"] for c in nulls)
            or sorted(tuple(c["conkey"]) for c in nulls) != expected_keys):
        return False
    if (sorted(c["contype"] for c in constraints if c["contype"] != "n") != list(kinds)
            or any(not c["enforced"] or not c["convalidated"] or c["condeferrable"] or c["condeferred"]
                   or (c["contype"] == "c" and c["connoinherit"]) or (c["contype"] == "f" and
                       (c["confupdtype"] != "a" or c["confmatchtype"] != "s"))
                   for c in constraints)):
        return False
    # Exactly the two immediate, unconditional, valid constraint-backed unique
    # indexes. Extra partial/expression uniqueness can also reject valid writes.
    indexes = bind.execute(sa.text("""
        SELECT i.indisvalid, i.indisready, i.indimmediate, i.indpred IS NULL AS plain,
               i.indexprs IS NULL AS simple, c.contype
        FROM pg_index i LEFT JOIN pg_constraint c
          ON c.conindid = i.indexrelid AND c.conrelid = i.indrelid AND c.contype IN ('p','u')
        WHERE i.indrelid = to_regclass(:table) AND i.indisunique
    """), {"table": table}).mappings().all()
    if (sorted(i["contype"] or "" for i in indexes) != list(unique)
            or any(not all(i[k] for k in ("indisvalid", "indisready", "indimmediate", "plain", "simple"))
                   for i in indexes)):
        return False
    return not bind.execute(sa.text("""
        SELECT EXISTS (
          SELECT 1 FROM pg_class r WHERE r.oid = to_regclass(:table)
            AND (r.relkind <> 'r' OR r.relpersistence <> 'p' OR r.relrowsecurity OR r.relforcerowsecurity)
          UNION ALL SELECT 1 FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
            WHERE a.attrelid = to_regclass(:table) AND a.attnum > 0 AND NOT a.attisdropped
              AND (a.attidentity <> '' OR a.attgenerated <> '' OR a.attcollation <> t.typcollation)
          UNION ALL SELECT 1 FROM pg_trigger WHERE tgrelid = to_regclass(:table) AND (NOT tgisinternal OR tgenabled <> 'O')
          UNION ALL SELECT 1 FROM pg_rewrite WHERE ev_class = to_regclass(:table)
          UNION ALL SELECT 1 FROM pg_inherits WHERE inhrelid = to_regclass(:table)
                                                   OR inhparent = to_regclass(:table))
    """), {"table": table}).scalar_one()


def _receipt_upgrade():
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
    valid = valid and [(u["name"], u["column_names"]) for u in
                       inspector.get_unique_constraints(TABLE)] == [
                           ("uq_training_compensation_operation", ["operation_id"])]
    schema = inspector.default_schema_name
    foreign = {(tuple(f["constrained_columns"]), f.get("referred_schema") or schema,
                f["referred_table"], tuple(f["referred_columns"]),
                f.get("options", {}).get("ondelete"),
                f.get("options", {}).get("onupdate", "NO ACTION"))
               for f in inspector.get_foreign_keys(TABLE)}
    valid = valid and foreign == {((col,), schema, target, ("id",), "CASCADE", "NO ACTION")
                                 for col, target in _foreign().items()}
    checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints(TABLE)}
    valid = valid and set(checks) == {"ck_training_compensation_proof", "ck_training_compensation_reason"}
    valid = valid and all(_fixed_check(checks.get(name, ""), column, literal)
                          for name, column, literal in (
        ("ck_training_compensation_proof", "proof_version", "TRACKED_TRAINING_V1"),
        ("ck_training_compensation_reason", "reason", "EXPIRED_ZERO_ATTEMPTS")))
    if bind.dialect.name == "postgresql":
        valid = valid and inspector.get_pk_constraint(TABLE)["name"] == TABLE + "_pkey"
        valid = valid and {f["name"] for f in inspector.get_foreign_keys(TABLE)} == {
            TABLE + "_" + column + "_fkey" for column in _foreign()}
        valid = valid and _postgres_contract(bind)
    if not valid:
        raise RuntimeError("incompatible training expiry compensation schema")


def upgrade():
    # Verify receipt authority first: incompatible adoption changes nothing.
    _receipt_upgrade()
    bind = op.get_bind()
    table = "ai_allowance_purge_progress"
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        op.create_table(table,
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
            sa.Column("after_id", sa.Integer(), nullable=False),
            sa.Column("through_id", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("id = 1", name="ck_ai_allowance_purge_singleton"),
            sa.CheckConstraint("after_id >= 0 AND through_id >= after_id", name="ck_ai_allowance_purge_progress"))
    else:
        columns = inspector.get_columns(table)
        checks = inspector.get_check_constraints(table)
        normalized = {c["name"]: "".join(c["sqltext"].replace("(", "").replace(")", "").split())
                      for c in checks}
        if ([(c["name"], c["type"].compile(dialect=bind.dialect), c["nullable"], c.get("default"))
             for c in columns] != [("id", "INTEGER", False, None), ("after_id", "INTEGER", False, None),
                                   ("through_id", "INTEGER", False, None)]
                or inspector.get_pk_constraint(table)["constrained_columns"] != ["id"]
                or inspector.get_foreign_keys(table) or inspector.get_unique_constraints(table)
                or normalized != {"ck_ai_allowance_purge_singleton": "id=1",
                                  "ck_ai_allowance_purge_progress": "after_id>=0ANDthrough_id>=after_id"}
                or (bind.dialect.name == "postgresql" and not _postgres_contract(
                    bind, table=table, kinds=("c", "c", "p"), unique=("p",),
                    not_null=("id", "after_id", "through_id")))):
            raise RuntimeError("incompatible allowance purge progress schema")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM " + TABLE + ")")).scalar():
        raise RuntimeError("committed compensation evidence must be retained")
    op.drop_table("ai_allowance_purge_progress")
    op.drop_table(TABLE)
