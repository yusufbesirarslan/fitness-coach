"""R6-01A: the expand/contract migration gate is executable, not a policy note.

Fixtures are synthetic migration sources written to a temporary versions
directory; the real chain is checked against the frozen historical baseline.
"""
import textwrap
from pathlib import Path

import pytest

from scripts import migration_expand_contract as gate

HEADER = '''\
from alembic import op
import sqlalchemy as sa

revision = "f00dfeed0001"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None
expand_contract = "expand"

'''


def _check(body, header=HEADER):
    source = header + textwrap.dedent(body)
    revision, classification, problems = gate.check_source(source)
    return classification, problems


# ── TEST 7: representative unsafe migrations are rejected ──────────────────

UNSAFE = {
    "drop_table": '''
        def upgrade():
            op.drop_table("meal_log")
        ''',
    "drop_column": '''
        def upgrade():
            op.drop_column("user", "language")
        ''',
    "batch_drop_column": '''
        def upgrade():
            with op.batch_alter_table("user") as batch_op:
                batch_op.drop_column("language")
        ''',
    "aliased_op_drop": '''
        from alembic import op as migration_ops

        def upgrade():
            migration_ops.drop_table("meal_log")
        ''',
    "imported_drop_by_name": '''
        from alembic.op import drop_column as add_nothing

        def upgrade():
            add_nothing("user", "language")
        ''',
    "drop_in_helper": '''
        def _cleanup():
            op.drop_column("user", "language")

        def upgrade():
            _cleanup()
        ''',
    "drop_at_module_level": '''
        op.drop_table("meal_log")

        def upgrade():
            pass
        ''',
    "rename_column": '''
        def upgrade():
            op.alter_column("user", "language", new_column_name="locale")
        ''',
    "batch_rename_column": '''
        def upgrade():
            with op.batch_alter_table("user") as batch_op:
                batch_op.alter_column("language", new_column_name="locale")
        ''',
    "rename_table": '''
        def upgrade():
            op.rename_table("meal_log", "meals")
        ''',
    "type_change": '''
        def upgrade():
            op.alter_column("user", "language", type_=sa.String(2))
        ''',
    "set_not_null": '''
        def upgrade():
            op.alter_column("user", "language", nullable=False)
        ''',
    "server_default_change": '''
        def upgrade():
            op.alter_column("user", "language", server_default="tr")
        ''',
    "not_null_column_without_default": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8), nullable=False))
        ''',
    "primary_key_column_without_default": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.Integer(), primary_key=True))
        ''',
    "non_literal_column": '''
        COLUMN = sa.Column("tier", sa.String(8))

        def upgrade():
            op.add_column("user", COLUMN)
        ''',
    "drop_constraint": '''
        def upgrade():
            op.drop_constraint("uq_user_email", "user", type_="unique")
        ''',
    "drop_index": '''
        def upgrade():
            op.drop_index("ix_meal_log_user_id", table_name="meal_log")
        ''',
    "unique_index": '''
        def upgrade():
            op.create_index("uq_x", "user", ["language"], unique=True)
        ''',
    "unique_constraint": '''
        def upgrade():
            op.create_unique_constraint("uq_x", "user", ["language"])
        ''',
    "foreign_key_on_existing_table": '''
        def upgrade():
            op.create_foreign_key("fk_x", "meal_log", "user", ["user_id"], ["id"])
        ''',
    "check_constraint": '''
        def upgrade():
            op.create_check_constraint("ck_x", "user", "length(language) = 2")
        ''',
    "raw_drop_table": '''
        def upgrade():
            op.execute("DROP TABLE IF EXISTS meal_log")
        ''',
    "raw_drop_via_text": '''
        def upgrade():
            op.execute(sa.text("ALTER TABLE meal_log DROP COLUMN notes"))
        ''',
    "raw_delete": '''
        def upgrade():
            op.get_bind().execute(sa.text("DELETE FROM meal_log WHERE id < 10"))
        ''',
    "raw_truncate": '''
        def upgrade():
            op.execute("TRUNCATE meal_log")
        ''',
    "raw_rename": '''
        def upgrade():
            op.execute('ALTER TABLE "user" RENAME COLUMN language TO locale')
        ''',
    "raw_type_change": '''
        def upgrade():
            op.execute('ALTER TABLE "user" ALTER COLUMN language TYPE VARCHAR(2)')
        ''',
    "raw_set_not_null": '''
        def upgrade():
            op.execute('ALTER TABLE "user" ALTER COLUMN language SET NOT NULL')
        ''',
    "raw_add_not_null_without_default": '''
        def upgrade():
            op.execute('ALTER TABLE "user" ADD COLUMN tier VARCHAR(8) NOT NULL')
        ''',
    "raw_add_column_with_fk": '''
        def upgrade():
            op.execute('ALTER TABLE meal_log ADD COLUMN owner_id INTEGER REFERENCES "user"(id)')
        ''',
    "raw_add_then_drop_in_one_statement": '''
        def upgrade():
            op.execute('ALTER TABLE "user" ADD COLUMN a INTEGER, DROP COLUMN language')
        ''',
    "raw_second_statement_hidden": '''
        def upgrade():
            op.execute("CREATE INDEX ix_a ON meal_log (user_id); DROP TABLE x")
        ''',
    "raw_unique_index": '''
        def upgrade():
            op.execute('CREATE UNIQUE INDEX uq_x ON "user" (language)')
        ''',
    "raw_function_block": '''
        def upgrade():
            op.execute("DO $$ BEGIN PERFORM 1; END $$")
        ''',
    "raw_sql_from_variable": '''
        SQL = "CREATE INDEX ix_a ON meal_log (user_id)"

        def upgrade():
            op.execute(SQL)
        ''',
    "raw_sql_fstring": '''
        def upgrade():
            table = "meal_log"
            op.execute(f"DROP TABLE {table}")
        ''',
    "raw_sql_concatenation": '''
        def upgrade():
            op.execute("DROP " + "TABLE meal_log")
        ''',
    "exec_driver_sql": '''
        def upgrade():
            op.get_bind().exec_driver_sql("DROP TABLE meal_log")
        ''',
    "ddl_construct": '''
        def upgrade():
            op.execute(sa.DDL("DROP TABLE meal_log"))
        ''',
    "table_drop_method": '''
        def upgrade():
            sa.Table("meal_log", sa.MetaData()).drop(op.get_bind())
        ''',
    "metadata_drop_all": '''
        def upgrade():
            sa.MetaData().drop_all(op.get_bind())
        ''',
    "core_delete": '''
        def upgrade():
            table = sa.table("meal_log")
            op.execute(table.delete())
        ''',
    "unique_index_object_create": '''
        def upgrade():
            sa.Index("uq_x", sa.Column("language"), unique=True).create(op.get_bind())
        ''',
    "getattr_dispatch": '''
        def upgrade():
            getattr(op, "drop_" + "table")("meal_log")
        ''',
    "eval": '''
        def upgrade():
            eval('op.drop_table("meal_log")')
        ''',
    "importlib": '''
        import importlib

        def upgrade():
            importlib.import_module("alembic.op").drop_table("meal_log")
        ''',
    "calls_downgrade": '''
        def upgrade():
            downgrade()

        def downgrade():
            op.drop_table("meal_log")
        ''',
    "rebinds_upgrade": '''
        def downgrade():
            op.drop_table("meal_log")

        def upgrade():
            pass

        upgrade = downgrade
        ''',
    "global_escape": '''
        def downgrade():
            global upgrade
            op.drop_table("meal_log")

        def upgrade():
            pass
        ''',
    "unpacked_alter_kwargs": '''
        def upgrade():
            op.alter_column("user", "language", **{"new_column_name": "locale"})
        ''',
    "server_default_text_statement": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8),
                          server_default=sa.text("'x'; DROP TABLE meal_log")))
        ''',
}


@pytest.mark.parametrize("name", sorted(UNSAFE))
def test_unsafe_forward_migration_is_rejected(name):
    classification, problems = _check(UNSAFE[name])
    assert classification == gate.EXPAND
    assert problems, f"{name} passed the expand/contract gate"


# ── ... and representative additive migrations are accepted ────────────────

SAFE = {
    "create_table": '''
        def upgrade():
            op.create_table(
                "streak_freeze",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id",
                          ondelete="CASCADE"), nullable=False),
                sa.Column("created_at", sa.DateTime(), nullable=False,
                          server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.UniqueConstraint("user_id", name="uq_streak_freeze_user"),
            )
        ''',
    "nullable_column": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8), nullable=True))
        ''',
    "default_nullable_column": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8)))
        ''',
    "not_null_with_server_default": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8), nullable=False,
                                            server_default="free"))
        ''',
    "batch_nullable_column": '''
        def upgrade():
            with op.batch_alter_table("user") as batch_op:
                batch_op.add_column(sa.Column("tier", sa.String(8), nullable=True))
        ''',
    "non_unique_index": '''
        def upgrade():
            op.create_index("ix_meal_log_created", "meal_log", ["created_at"])
        ''',
    "partial_index": '''
        def upgrade():
            op.create_index("ix_open", "workout_session", ["user_id"],
                            unique=False,
                            postgresql_where=sa.text("status = 'IN_PROGRESS'"))
        ''',
    "relax_not_null": '''
        def upgrade():
            op.alter_column("user", "password_hash", nullable=True,
                            existing_type=sa.String(256))
        ''',
    "raw_add_column_if_not_exists": '''
        def upgrade():
            op.execute('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS tier VARCHAR(8)')
        ''',
    "raw_add_not_null_with_default": '''
        def upgrade():
            op.execute("ALTER TABLE meal_log ADD COLUMN source VARCHAR(8) NOT NULL DEFAULT 'web'")
        ''',
    "raw_create_index_and_backfill": '''
        def upgrade():
            op.execute("CREATE INDEX IF NOT EXISTS ix_a ON meal_log (user_id)")
            op.execute(sa.text("UPDATE meal_log SET source = 'web' WHERE source IS NULL"))
        ''',
    "raw_create_table_with_own_constraints": '''
        def upgrade():
            op.execute("""
                CREATE TABLE IF NOT EXISTS pump_check_like (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    UNIQUE (user_id)
                )
            """)
        ''',
    "downgrade_may_contract": '''
        def upgrade():
            op.add_column("user", sa.Column("tier", sa.String(8), nullable=True))

        def downgrade():
            op.drop_column("user", "tier")
        ''',
    "inspect_guarded_create": '''
        def upgrade():
            bind = op.get_bind()
            if not sa.inspect(bind).has_table("streak_freeze"):
                op.create_table("streak_freeze",
                                sa.Column("id", sa.Integer(), primary_key=True))
        ''',
}


@pytest.mark.parametrize("name", sorted(SAFE))
def test_additive_forward_migration_is_accepted(name):
    classification, problems = _check(SAFE[name])
    assert classification == gate.EXPAND
    assert problems == []


# ── metadata: declared, literal, and contract needs a reason ───────────────

def test_forward_migration_must_declare_its_compatibility():
    header = HEADER.replace('expand_contract = "expand"\n', "")
    _, problems = _check(SAFE["nullable_column"], header=header)
    assert problems and "expand_contract" in problems[0]


@pytest.mark.parametrize("value", ['"Expand"', '"maybe"', "EXPAND", "True"])
def test_compatibility_declaration_must_be_a_known_literal(value):
    header = HEADER.replace('"expand"', value)
    _, problems = _check(SAFE["nullable_column"], header=header)
    assert problems


def test_contract_migration_needs_a_reviewed_reason():
    header = HEADER.replace('"expand"', '"contract"')
    classification, problems = _check(UNSAFE["drop_column"], header=header)
    assert classification == gate.CONTRACT
    assert problems and "expand_contract_reason" in problems[0]

    header += 'expand_contract_reason = "column unused since rev X; release runs without overlap"\n'
    classification, problems = _check(UNSAFE["drop_column"], header=header)
    assert (classification, problems) == (gate.CONTRACT, [])


def test_contract_revisions_are_reported_for_deploy_control(tmp_path):
    header = HEADER.replace('"expand"', '"contract"') + (
        'expand_contract_reason = "column unused since rev X; release runs without overlap"\n')
    (tmp_path / "f00dfeed0001_drop.py").write_text(
        header + textwrap.dedent(UNSAFE["drop_column"]), encoding="utf-8")
    result = gate.scan(tmp_path, baseline={})
    assert result["violations"] == {}
    assert result["classes"] == {"f00dfeed0001": gate.CONTRACT}
    assert gate.contract_revisions(tmp_path) == ["f00dfeed0001"]


# ── TEST 8: the shipped chain is valid without rewriting history ───────────

def test_shipped_migration_chain_passes_the_gate():
    result = gate.scan()
    assert result["violations"] == {}
    assert gate.main([]) == 0


def test_historical_baseline_is_closed_and_frozen():
    """The baseline is exactly the chain shipped before R6-01A (base 9c05d21).

    Growing it would silently exempt a new migration; the count and the
    revision set are pinned so that requires editing this test as well.
    """
    assert len(gate.HISTORICAL_BASELINE) == 46
    import hashlib
    digest = hashlib.sha256(
        ",".join(sorted(gate.HISTORICAL_BASELINE)).encode()).hexdigest()
    assert digest == (
        "5f25b7b3adec61c454f6f8212e2a52315aeb6fc09300ffae765e665bab57c648")


def test_every_shipped_revision_is_in_the_baseline_or_forward_checked():
    import ast
    revisions = set()
    for path in gate.VERSIONS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revisions.add(gate._module_literal(tree, "revision"))
    assert set(gate.HISTORICAL_BASELINE) <= revisions
    assert gate.scan()["classes"].keys() == revisions


def test_baseline_is_what_exempts_history_not_a_lenient_rule_set(tmp_path):
    """Strict rules would reject real shipped migrations (e.g. the meal_log
    NOT NULL tightening and the user_daily_nutrition drop). History passes
    only because it is pinned — the rules themselves are not weakened."""
    rejected = set()
    for path in gate.VERSIONS_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8") + '\nexpand_contract = "expand"\n'
        _, _, problems = gate.check_source(source, path.name)
        if problems:
            rejected.add(path.name.split("_", 1)[0])
    assert {"b8c9d0e1f2a3", "f6a7b8c9d0e1", "c5d6e7f8a9b0"} <= rejected


def test_modifying_a_historical_migration_is_a_violation(tmp_path):
    source_path = next(gate.VERSIONS_DIR.glob("f6a7b8c9d0e1_*.py"))
    original = source_path.read_bytes()
    (tmp_path / source_path.name).write_bytes(original + b"\n# edited\n")
    baseline = {"f6a7b8c9d0e1": gate.HISTORICAL_BASELINE["f6a7b8c9d0e1"]}
    violations = gate.scan(tmp_path, baseline=baseline)["violations"]
    assert "immutable" in violations[source_path.name][0]


def test_digest_ignores_checkout_line_endings(tmp_path):
    source_path = next(gate.VERSIONS_DIR.glob("f6a7b8c9d0e1_*.py"))
    lf = source_path.read_bytes().replace(b"\r\n", b"\n")
    assert gate.normalized_digest(lf) == gate.normalized_digest(
        lf.replace(b"\n", b"\r\n"))


def test_removing_a_historical_migration_is_a_violation(tmp_path):
    violations = gate.scan(tmp_path, baseline={"deadbeef0000": "0" * 64})["violations"]
    assert "missing" in violations["<baseline>"][0]


def test_a_new_unsafe_migration_in_the_real_layout_fails_the_scan(tmp_path):
    for path in gate.VERSIONS_DIR.glob("*.py"):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "f00dfeed0001_unsafe.py").write_text(
        HEADER + textwrap.dedent(UNSAFE["drop_column"]), encoding="utf-8")
    violations = gate.scan(tmp_path)["violations"]
    assert list(violations) == ["f00dfeed0001_unsafe.py"]


def test_gate_module_is_runnable_without_the_application():
    """CI and humans can run it before any app import (no env needed)."""
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
