"""Dormant Nutrition edit history; expand only, old-code writes remain valid.

No old plan bytes change. Backfill per-row opaque identity, version zero, no
invented history. Roll back code while retaining this schema to preserve data.
Downgrade refuses to destroy any operation or history and is safe only empty.

Re-landed C1 (#534) after owner-private exercises. The schema, backfill,
verification and downgrade below are byte-identical to the accepted
``c1a9b7e5d301``; only the identity and parent changed. ``c1a9b7e5d301`` is
retired: it was parented on ``ad17de1e0001`` and was reverted (05147a04), so a
database still stamped with it has this schema but NOT ``private_exercise``.
``app/retired_revisions.py`` refuses such a database before any migration
runs and owns its one verified, opt-in reconciliation.
"""
import secrets
import re
from alembic import op
import sqlalchemy as sa

revision = "19cb998acd2f"
down_revision = "e5b27aa4101c"
branch_labels = None
depends_on = None
expand_contract = "expand"


def _create_operations():
    op.create_table("nutrition_plan_edit_operation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("command_payload", sa.JSON, nullable=False),
        sa.Column("plan_lineage_id", sa.String(64), nullable=False),
        sa.Column("base_version", sa.Integer, nullable=False),
        sa.Column("before_snapshot", sa.Text, nullable=False),
        sa.Column("after_snapshot", sa.Text),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("proposal_public_id", sa.String(64)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_nutrition_edit_user_key"),
        sa.CheckConstraint("status IN ('estimating','prepared','failed','applied','cancelled','stale','no_op')", name="ck_nutrition_edit_status"),
        sa.Index("ix_nutrition_edit_owner", "user_id"),
        sa.Index("uq_nutrition_edit_active_owner", "user_id", unique=True,
                 sqlite_where=sa.text("status IN ('estimating', 'prepared')"),
                 postgresql_where=sa.text("status IN ('estimating', 'prepared')")))


def _create_journal():
    op.create_table("nutrition_plan_mutation_record",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_lineage_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("operation_kind", sa.String(20), nullable=False),
        sa.Column("before_version", sa.Integer, nullable=False),
        sa.Column("after_version", sa.Integer, nullable=False),
        sa.Column("before_snapshot", sa.Text, nullable=False),
        sa.Column("after_snapshot", sa.Text, nullable=False),
        sa.Column("nutrient_source", sa.String(64)),
        sa.Column("reverts_mutation_id", sa.Integer),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_nutrition_mutation_user_key"),
        sa.UniqueConstraint("reverts_mutation_id", name="uq_nutrition_mutation_reverts"),
        sa.CheckConstraint("operation_kind IN ('mutation','undo')", name="ck_nutrition_mutation_kind"),
        sa.Index("ix_nutrition_mutation_lineage", "user_id", "plan_lineage_id", "id"))


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nutrition_plan")}
    if "lineage_id" not in cols:
        op.add_column("nutrition_plan", sa.Column("lineage_id", sa.String(64), nullable=True))
    if "mutation_version" not in cols:
        op.add_column("nutrition_plan", sa.Column("mutation_version", sa.Integer,
                                                  nullable=False, server_default="0"))
    # Old code can still INSERT its original column set during code rollback.
    for (pk,) in bind.execute(sa.text("SELECT id FROM nutrition_plan WHERE lineage_id IS NULL")):
        bind.execute(sa.text("UPDATE nutrition_plan SET lineage_id=:v WHERE id=:id"),
                     {"v": secrets.token_urlsafe(32), "id": pk})
    if not sa.inspect(bind).has_table("nutrition_plan_edit_operation"):
        _create_operations()
    if not sa.inspect(bind).has_table("nutrition_plan_mutation_record"):
        _create_journal()
    _verify(bind)


def _normalize(sql):
    sql = re.sub(r"::(?:text|character varying|integer)(?:\[\])?", "", sql.lower())
    sql = sql.replace("= any", "in").replace("array[", "").replace("]", "")
    return re.sub(r'[\s()"]', '', sql)


def _verify(bind):
    inspector = sa.inspect(bind)
    plan = {c["name"]: c for c in inspector.get_columns("nutrition_plan")}
    if (str(plan["lineage_id"]["type"]) != "VARCHAR(64)" or not plan["lineage_id"]["nullable"]
            or str(plan["mutation_version"]["type"]) != "INTEGER" or plan["mutation_version"]["nullable"]
            or _normalize(str(plan["mutation_version"]["default"])).strip("'") != "0"):
        raise RuntimeError("Nutrition plan version schema mismatch")
    for table, spec in SCHEMA.items():
        columns = {c["name"]: ("DATETIME" if isinstance(c["type"], sa.DateTime) else str(c["type"]), c["nullable"])
                   for c in inspector.get_columns(table)}
        unique = sorted(tuple(c["column_names"]) for c in inspector.get_unique_constraints(table))
        checks = {c["name"]: _normalize(c["sqltext"]) for c in inspector.get_check_constraints(table)}
        indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]),
                   _normalize(str(i.get("dialect_options", {}).get(bind.dialect.name + "_where", ""))))
                   for i in inspector.get_indexes(table) if not i.get("duplicates_constraint")}
        expected_indexes = {k: (v[0], bool(v[1]), _normalize(v[2])) for k, v in spec["indexes"].items()}
        fk = inspector.get_foreign_keys(table)
        if (columns != spec["columns"] or unique != spec["unique"]
                or checks != {k: _normalize(v) for k, v in spec["checks"].items()}
                or indexes != expected_indexes
                or inspector.get_pk_constraint(table).get("constrained_columns") != ["id"]
                or len(fk) != 1 or fk[0]["constrained_columns"] != ["user_id"]
                or fk[0]["referred_table"] != "user" or fk[0]["referred_columns"] != ["id"]
                or fk[0].get("options", {}).get("ondelete") != "CASCADE"):
            raise RuntimeError("Nutrition edit authority schema mismatch")


def downgrade():
    bind = op.get_bind()
    for table in ("nutrition_plan_edit_operation", "nutrition_plan_mutation_record"):
        if sa.inspect(bind).has_table(table) and bind.execute(
                sa.text("SELECT count(*) FROM " + table)).scalar():
            raise RuntimeError("Nutrition edit history exists; retain schema and roll back code instead")
    for table in ("nutrition_plan_edit_operation", "nutrition_plan_mutation_record"):
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    cols = {c["name"] for c in sa.inspect(bind).get_columns("nutrition_plan")}
    with op.batch_alter_table("nutrition_plan") as batch:
        for name in ("lineage_id", "mutation_version"):
            if name in cols:
                batch.drop_column(name)


# Frozen authority shape, also verifies db.create_all-before-upgrade boots.
SCHEMA = {'nutrition_plan_edit_operation': {'checks': {'ck_nutrition_edit_status': 'status '
                                                                          'IN '
                                                                          "('estimating','prepared','failed','applied','cancelled','stale','no_op')"},
                                   'columns': {'after_snapshot': ('TEXT', True),
                                               'base_version': ('INTEGER',
                                                                False),
                                               'before_snapshot': ('TEXT',
                                                                   False),
                                               'command_fingerprint': ('VARCHAR(64)',
                                                                       False),
                                               'command_payload': ('JSON',
                                                                   False),
                                               'created_at': ('DATETIME',
                                                              False),
                                               'failure_code': ('VARCHAR(64)',
                                                                True),
                                               'id': ('INTEGER', False),
                                               'idempotency_key': ('VARCHAR(64)',
                                                                   False),
                                               'plan_lineage_id': ('VARCHAR(64)',
                                                                   False),
                                               'proposal_public_id': ('VARCHAR(64)',
                                                                      True),
                                               'public_id': ('VARCHAR(64)',
                                                             False),
                                               'status': ('VARCHAR(20)', False),
                                               'user_id': ('INTEGER', False)},
                                   'indexes': {'ix_nutrition_edit_owner': (('user_id',),
                                                                           False,
                                                                           ''),
                                               'uq_nutrition_edit_active_owner': (('user_id',),
                                                                                  True,
                                                                                  'status '
                                                                                  'IN '
                                                                                  "('estimating', "
                                                                                  "'prepared')")},
                                   'unique': [('public_id',),
                                              ('user_id', 'idempotency_key')]},
 'nutrition_plan_mutation_record': {'checks': {'ck_nutrition_mutation_kind': 'operation_kind '
                                                                             'IN '
                                                                             "('mutation','undo')"},
                                    'columns': {'after_snapshot': ('TEXT',
                                                                   False),
                                                'after_version': ('INTEGER',
                                                                  False),
                                                'before_snapshot': ('TEXT',
                                                                    False),
                                                'before_version': ('INTEGER',
                                                                   False),
                                                'command_fingerprint': ('VARCHAR(64)',
                                                                        False),
                                                'created_at': ('DATETIME',
                                                               False),
                                                'id': ('INTEGER', False),
                                                'idempotency_key': ('VARCHAR(64)',
                                                                    False),
                                                'nutrient_source': ('VARCHAR(64)',
                                                                    True),
                                                'operation_kind': ('VARCHAR(20)',
                                                                   False),
                                                'plan_lineage_id': ('VARCHAR(64)',
                                                                    False),
                                                'public_id': ('VARCHAR(64)',
                                                              False),
                                                'reverts_mutation_id': ('INTEGER',
                                                                        True),
                                                'user_id': ('INTEGER', False)},
                                    'indexes': {'ix_nutrition_mutation_lineage': (('user_id',
                                                                                   'plan_lineage_id',
                                                                                   'id'),
                                                                                  False,
                                                                                  '')},
                                    'unique': [('public_id',),
                                               ('reverts_mutation_id',),
                                               ('user_id', 'idempotency_key')]}}
