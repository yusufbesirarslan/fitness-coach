"""R6-01A expand/contract gate for Alembic migrations.

During a dual-revision release the PREVIOUS revision keeps serving while the
schema moves to the new head. That is only safe if every new migration is an
EXPAND step: the old code must keep working against the new schema. This module
turns that policy into a deterministic, static (AST) check that runs in the
normal pytest suite (tests/test_migration_expand_contract.py) and from the
command line:

    python -m scripts.migration_expand_contract

Boundary
--------
* ``HISTORICAL_BASELINE`` pins every migration that shipped before this
  contract, by revision AND by normalized content digest. Those files are
  exempt from the rules (they are production history and are never rewritten),
  but they may not change: a digest mismatch is a violation. The baseline is
  closed — a revision that is not in it is a forward migration.
* Every forward migration must declare, as module-level string literals::

      expand_contract = "expand"      # blue/green-compatible; rules enforced
  or
      expand_contract = "contract"    # NOT blue/green-compatible
      expand_contract_reason = "why, and how the release is sequenced"

  "contract" is the explicit, reviewed escape for genuinely destructive steps.
  It does not make the change safe to overlap; it records that the release
  carrying it must not run two revisions at once. ``contract_revisions()``
  exposes that set to future deploy control.
* For "expand" migrations everything outside ``def downgrade`` is checked
  (module level and helpers included, so moving a drop into a helper does not
  hide it). A construct the checker cannot prove safe is a violation: unknown
  is treated as unsafe.

Rejected in an expand migration
-------------------------------
drop_table / drop_column / drop_constraint / drop_index / rename_table /
drop_table_comment / .drop() / .drop_all() / .delete() — on any receiver
(``op``, ``batch_op``, an alias), including ``from alembic.op import X``;
alter_column with anything but ``nullable=True`` / comment / ``existing_*``
(type change, rename, NOT NULL, server_default change);
add_column of a NOT NULL / primary-key column without a server_default, or of
a column the checker cannot see literally;
create_index(unique=...) other than literal False, create_unique_constraint,
create_check_constraint, create_foreign_key, create_primary_key,
create_exclude_constraint (they can make the old revision's writes fail);
raw SQL (execute / exec_driver_sql / DDL) unless it is a string literal (or
text() of one) whose every statement starts with CREATE TABLE, CREATE INDEX
(non-unique), CREATE SEQUENCE, INSERT, UPDATE, SELECT or COMMENT ON and contains
none of DROP, TRUNCATE, DELETE, RENAME, ALTER, GRANT, REVOKE, UNIQUE (a new
table's own UNIQUE / ON DELETE clauses are allowed), or is a single
``ALTER TABLE t ADD COLUMN`` without REFERENCES/CHECK/UNIQUE/PRIMARY/TYPE and
without NOT NULL unless it has a DEFAULT; a bare text() expression (default,
index predicate) must be a statement-free literal;
dynamic dispatch (getattr, setattr, exec, eval, __import__, importlib),
``global``/``nonlocal``, calling ``downgrade()`` or rebinding ``upgrade``.

Accepted: create_table, add_column of a nullable column (or NOT NULL with a
server_default), non-unique create_index, alter_column(nullable=True),
literal additive SQL and data backfills (UPDATE/INSERT).

Known limits (static analysis): a literal UPDATE is assumed to backfill new
columns — rewriting a column the old revision reads is not detectable here and
stays a review responsibility; a FOREIGN KEY inside a NEW table is accepted;
identifiers that collide with keywords (a column named ``type``) are rejected
in raw SQL — use op.add_column instead. Everything else unknown fails closed.
"""
import ast
import hashlib
import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"

# Frozen at R6-01A (base main 9c05d21). Never add to this map: a new migration
# is a forward migration by definition.
HISTORICAL_BASELINE = {
    "54f2eb195404":
        "2938eba4399ad5f97d76f366078a02672814fcc85b85884237293508248fa732",
    "9be792c80008":
        "a03246bc17e14ad800ff14a687f734b91791d3e14204fdd9783c7b4bf97ac887",
    "a1b2c3d4e5f6":
        "663cc45a019cc6f01f96ff724adbf65fa22be05080fd6f189e24e15c3211c6c6",
    "a6b7c8d9e0f1":
        "ed756a8090f1087e4c1c88b1941b3d08fc81f837bf7ca02576ee958795291f82",
    "a7b8c9d0e1f2":
        "421ed258835f05581ecde38c33b0cf78456f749e02ed98c8c2b204d9834b7cfa",
    "a994f9bed783":
        "a093dd15f9502cf30e5e867b6683df52fccc3d5995566f81e38e72a41682c858",
    "aa11bb22cc33":
        "64fa2fbebd5c269969ed2c18df0fc1be53125c14c87eae6e9de7b8c3fe7c886d",
    "aa77bb88cc99":
        "4ea0401d1b9e468399d518125c74a9dc79947f252d1a4fc82ca99484e3d8068f",
    "ab12cd34ef56":
        "851226dcfdb68b238594fd8151753a45ba9e5a902db8cbb85c8f532c6dc4fd5f",
    "b1c2d3e4f5a6":
        "48224c8565dc5542c3bbbb19a8665efd98e27fadb64c47a0fc545ffa1dd010e8",
    "b2c3d4e5f6a7":
        "2a30227c4f57b9addfff4a21fcf1b751967592a04643c4e1dc5a9b148bfed3ee",
    "b3c4d5e6f7a8":
        "6fdf0a5ffbcbbf464302f43e51ac22488c9250f39e5c9844c55287a5004f7bbf",
    "b7c8d9e0f1a2":
        "2de281e02d30651d3c939ce2a8c6bf8a06a417d1fc9d4ea6c7051ea1a3089965",
    "b8c9d0e1f2a3":
        "2e9dbbb6f1a44a6ca82563340d5fb73c6d5a30bfd99d8983ba876d8d74b1686e",
    "bb22cc33dd44":
        "8f45fc0782765a652dfe58177deef7996f1f3f2da0f90bb871181551ea6f1ce3",
    "bb88cc99dd00":
        "bbb28023d78cf202a8fe066aa8f58179de3f8690ceb0b68ee059e6b15d2eea25",
    "c1d2e3f4a5b6":
        "7644f76d3780939c263f67852c064ebc0ad86294959535d5d1d3dc24ae5fbaae",
    "c2d3e4f5a6b7":
        "8a94968ab67d832d6eb7a0c28d32797f028e693b9bfce797f8d0b83bd8d0da89",
    "c3d4e5f6a7b8":
        "2ffa756b4d8b4c0ca0f3b44c11331cddf42e6a927f865b34608b5aab619c0a25",
    "c4d5e6f7a8b9":
        "1388c694cf7b4b991bf5a8a9c9be0657ec9581fbfd1751731b518e7e8b429ea6",
    "c5d6e7f8a9b0":
        "94e9911b4ba5d92076147e75f8ab67ed8e93718ecfe38596f0a0a924d719be44",
    "c7d8e9f0a1b2":
        "f38671a75077f7bc7053fbb500ca03110e3837595d2959c7972111352e01c6be",
    "c8d9e0f1a2b3":
        "e5e1cd4f467751087b8ac524227ad752aff37327e2fd10117502c1fd8edbf9a8",
    "cc33dd44ee55":
        "960fbb50ce6c790c10530f750efaf6358af79ed336abed5ca8b2d7c67fde7638",
    "d0e1f2a3b4c5":
        "7d7cf4433b99afb52e62293092eab3005680a71b43135c5a5f5d02bcb7feced2",
    "d3e4f5a6b7c8":
        "2d876f0ceaa6574022008ef34958e67ed80639784b97cf2bb73121599021df0d",
    "d4e5f6a7b8c9":
        "d2ebecafa19ed9e596e5e8a6b3f94adf394e60aeb2e0f4a625ea9d2b8fe8117b",
    "d6e7f8a9b0c1":
        "6b04c618e38996696ddcf6e1d58f294c0b492c4778059f5d1958159e4f7e7850",
    "d8e9f0a1b2c3":
        "020174b2750d552ffa5ebe5d9135861a52f937428ed6cfd7adc9acb19d7a5459",
    "dd44ee55ff66":
        "33061d820fa30430337999b7aa718c63580ae4b8a55a953c7b7dcc71f0962812",
    "df0d08c0cd24":
        "4a38abceefa71e785db42b001f19a158f330c6c16c2369edab4250ed0ff51152",
    "e1f2a3b4c5d6":
        "7fb530d15d57bdffff613c0a4790c0019f75ac6ed907513bfb5292819a8afa7b",
    "e2f3a4b5c6d7":
        "ab56ff43503537d89631ff5279a913cb79402ec90ad2ef262ffd64d9fbde78e2",
    "e4f5a6b7c8d9":
        "e14da652adbfd593cf00bde68f2d7e53b463bd7cd15290e85bfd3a5e0cb80cb4",
    "e5f6a7b8c9d0":
        "7c1daca32c9b950141cd69e85468d5c1ffccb937eff7c969e4a40673515697e3",
    "e7f8a9b0c1d2":
        "ab28eca2a331aa43a84fc8a24d22a0cd7dee8390a448c41a39be09023781550d",
    "e9f0a1b2c3d4":
        "89ec0c564d949cd33ddbadb421bdeed2778482032c901b1544a3d064dd675beb",
    "ee55ff66aa77":
        "bc47ff8e10c9352d04aa885545ee5b3c2b8dbbd5c566ab3ac8f72850194cb668",
    "f0a1b2c3d4e5":
        "e4bfa89e1519f286edccb66ba51d8fa89e0982c9813db94b53d7c87c10437c64",
    "f1a2b3c4d5e6":
        "25081b2df50a617df50e597d80af0ecb30ea0c2bca808697731cd460b49c4a61",
    "f2a3b4c5d6e7":
        "de953e36cfa0698c0fcec847c5a2f890cb575db847f07fe99d8dd51586a70f8a",
    "f5a6b7c8d9e0":
        "ae55e757b52405de2ca060a712d60b48b190e9817aa0b28a4a9e9ac8e262a1d1",
    "f6a7b8c9d0e1":
        "eccb79a11b0108664721e19ed8517cde486bb774e613568f2c8af8087885cb4b",
    "f8a9b0c1d2e3":
        "9331158aa0679095a246207ede82ce7d026ba4c5a77b0e08fe65151c0f77b662",
    "fa1b2c3d4e5f":
        "467e14825af1d6fc58e80412029f9eeb3e35dac7048ad1cb533c9d2b2b1f029c",
    "ff66aa77bb88":
        "6c2a6dc1d14133725a0bb78dddcf5786b6d07d2f67978291738c3c5a48901897",
}

EXPAND = "expand"
CONTRACT = "contract"

_DESTRUCTIVE_CALLS = frozenset({
    "drop_table", "drop_column", "drop_constraint", "drop_index",
    "rename_table", "drop_table_comment", "drop_column_comment", "drop",
    "drop_all", "delete",
})
_REVIEW_CALLS = frozenset({
    "create_unique_constraint", "create_check_constraint",
    "create_foreign_key", "create_primary_key", "create_exclude_constraint",
    "invoke", "implementation_for", "register_operation",
    # SchemaItem.create(bind) (e.g. a unique sa.Index) bypasses op.create_*.
    "create",
})
_DYNAMIC_CALLS = frozenset({"getattr", "setattr", "exec", "eval",
                            "__import__", "import_module"})
# Statement sinks: their first argument is executed as SQL. text() on its own
# only builds an expression (a default, a partial-index predicate) and is
# checked as one.
_RAW_SQL_CALLS = frozenset({"execute", "exec_driver_sql", "DDL"})
_ALTER_ALLOWED_KEYWORDS = frozenset({
    "nullable", "comment", "existing_type", "existing_nullable",
    "existing_server_default", "existing_comment", "schema",
})
_SQL_ALLOWED_START = re.compile(
    r"^(CREATE\s+TABLE|CREATE\s+INDEX|CREATE\s+SEQUENCE|INSERT|UPDATE|SELECT"
    r"|COMMENT\s+ON)\b", re.IGNORECASE)
_SQL_FORBIDDEN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|RENAME|ALTER|GRANT|REVOKE|UNIQUE)\b",
    re.IGNORECASE)
# A NEW table's own constraints (UNIQUE, ON DELETE ...) bind nothing the
# serving revision writes today.
_SQL_CREATE_TABLE = re.compile(r"^CREATE\s+TABLE\b", re.IGNORECASE)
_SQL_CREATE_TABLE_FORBIDDEN = re.compile(
    r"\b(DROP|TRUNCATE|RENAME|ALTER|GRANT|REVOKE)\b|;", re.IGNORECASE)
# The one ALTER form that is additive: a single ADD COLUMN action. Anything that
# could constrain the serving revision (NOT NULL without DEFAULT, REFERENCES,
# CHECK, PRIMARY KEY, UNIQUE, a second action) is rejected.
_SQL_ADD_COLUMN = re.compile(
    r"^ALTER\s+TABLE\s+(IF\s+EXISTS\s+)?(ONLY\s+)?\S+\s+ADD\s+COLUMN\s+",
    re.IGNORECASE)
_SQL_ADD_COLUMN_FORBIDDEN = re.compile(
    r"\b(DROP|TRUNCATE|DELETE|RENAME|GRANT|REVOKE|UNIQUE|REFERENCES|CHECK"
    r"|PRIMARY|TYPE|USING|ALTER)\b|,\s*(ADD|DROP|ALTER|RENAME)\b",
    re.IGNORECASE)


def normalized_digest(source_bytes):
    return hashlib.sha256(source_bytes.replace(b"\r\n", b"\n")).hexdigest()


def _module_literal(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant):
                return node.value.value
            return _NOT_LITERAL
    return None


_NOT_LITERAL = object()


def _call_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def _check_sql_literal(sql):
    problems = []
    for statement in _strip_sql_comments(sql).split(";"):
        statement = statement.strip()
        if not statement:
            continue
        add_column = _SQL_ADD_COLUMN.match(statement)
        if add_column:
            rest = statement[add_column.end():]
            if _SQL_ADD_COLUMN_FORBIDDEN.search(rest):
                problems.append(
                    f"ADD COLUMN carries a constraining clause: {statement[:60]!r}")
            elif (re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE)
                  and not re.search(r"\bDEFAULT\b", rest, re.IGNORECASE)):
                problems.append(
                    f"NOT NULL column without DEFAULT: {statement[:60]!r}")
        elif not _SQL_ALLOWED_START.match(statement):
            problems.append(f"raw SQL statement is not additive: {statement[:60]!r}")
        elif (_SQL_CREATE_TABLE_FORBIDDEN if _SQL_CREATE_TABLE.match(statement)
              else _SQL_FORBIDDEN).search(statement):
            problems.append(f"raw SQL contains a destructive keyword: {statement[:60]!r}")
    return problems


def _literal_sql_argument(node):
    """The SQL of execute("...") / execute(text("...")), or None."""
    if not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if (isinstance(arg, ast.Call) and _call_name(arg.func) == "text"
            and len(arg.args) == 1 and not arg.keywords
            and isinstance(arg.args[0], ast.Constant)
            and isinstance(arg.args[0].value, str)):
        return arg.args[0].value
    return None


def _column_call(node):
    for arg in node.args:
        if isinstance(arg, ast.Call) and _call_name(arg.func) == "Column":
            return arg
    return None


def _keyword(call, name):
    for kw in call.keywords:
        if kw.arg == name:
            return kw
    return None


def _is_literal(kw, value):
    return (kw is not None and isinstance(kw.value, ast.Constant)
            and kw.value.value is value)


def _check_call(node):
    name = _call_name(node.func)
    line = node.lineno
    if name is None:
        return [f"line {line}: call through a computed expression cannot be proven safe"]
    if name in _DESTRUCTIVE_CALLS:
        return [f"line {line}: {name}() is destructive for the serving revision"]
    if name in _REVIEW_CALLS:
        return [f"line {line}: {name}() can reject the serving revision's writes"]
    if name in _DYNAMIC_CALLS:
        return [f"line {line}: {name}() is dynamic and cannot be proven safe"]
    if name in ("downgrade",):
        return [f"line {line}: upgrade path calls downgrade()"]
    if name == "alter_column":
        problems = []
        if len(node.args) > 2 or any(kw.arg is None for kw in node.keywords):
            problems.append(f"line {line}: alter_column with extra/unpacked arguments")
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if kw.arg not in _ALTER_ALLOWED_KEYWORDS:
                problems.append(f"line {line}: alter_column({kw.arg}=...) changes the "
                                "serving revision's contract")
            elif kw.arg == "nullable" and not _is_literal(kw, True):
                problems.append(f"line {line}: alter_column nullable must be literal True")
        return problems
    if name == "add_column":
        column = _column_call(node)
        if column is None:
            return [f"line {line}: add_column without a literal Column(...)"]
        if any(kw.arg is None for kw in column.keywords):
            return [f"line {line}: add_column Column(**...) cannot be proven safe"]
        nullable = _keyword(column, "nullable")
        primary = _keyword(column, "primary_key")
        default = _keyword(column, "server_default")
        if nullable is not None and not isinstance(nullable.value, ast.Constant):
            return [f"line {line}: add_column nullable is not a literal"]
        required = _is_literal(nullable, False) or (
            primary is not None and not _is_literal(primary, False))
        has_default = default is not None and not (
            isinstance(default.value, ast.Constant) and default.value.value is None)
        if required and not has_default:
            return [f"line {line}: NOT NULL column without server_default breaks the "
                    "serving revision's inserts"]
        return []
    if name == "create_index":
        unique = _keyword(node, "unique")
        if any(kw.arg is None for kw in node.keywords):
            return [f"line {line}: create_index(**...) cannot be proven non-unique"]
        if unique is not None and not _is_literal(unique, False):
            return [f"line {line}: unique create_index can reject the serving "
                    "revision's writes"]
        return []
    if name == "text":
        return _check_text_expression(node)
    if name in _RAW_SQL_CALLS:
        sql = _literal_sql_argument(node)
        if sql is None:
            return [f"line {line}: {name}() with non-literal SQL cannot be proven safe"]
        return [f"line {line}: {p}" for p in _check_sql_literal(sql)]
    return []


class _Scope(ast.NodeVisitor):
    def __init__(self):
        self.problems = []

    def visit_FunctionDef(self, node):
        if node.name == "downgrade":
            # Contract steps are expected when rolling a migration back, but
            # downgrade() must not reach into module scope (e.g. rebind upgrade).
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Global, ast.Nonlocal)):
                    self.problems.append(
                        f"line {inner.lineno}: global/nonlocal in downgrade()")
            return
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Global(self, node):
        self.problems.append(f"line {node.lineno}: global statement in a migration")

    def visit_Nonlocal(self, node):
        self.problems.append(f"line {node.lineno}: nonlocal statement in a migration")

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if (alias.name in _DESTRUCTIVE_CALLS | _REVIEW_CALLS
                    or alias.name == "*" or alias.name == "importlib"):
                self.problems.append(
                    f"line {node.lineno}: imports {alias.name} by name")
        if node.module == "importlib":
            self.problems.append(f"line {node.lineno}: imports importlib")

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name.split(".")[0] == "importlib":
                self.problems.append(f"line {node.lineno}: imports importlib")

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in ("upgrade", "downgrade"):
                self.problems.append(f"line {node.lineno}: rebinds {target.id}")
        self.generic_visit(node)

    def visit_Call(self, node):
        self.problems.extend(_check_call(node))
        first = node.args[0] if node.args else None
        for child in ast.iter_child_nodes(node):
            if (child is first and _call_name(node.func) in _RAW_SQL_CALLS
                    and isinstance(child, ast.Call)
                    and _call_name(child.func) == "text"):
                continue  # execute(text("...")) is judged once, as a statement
            self.visit(child)


def _check_text_expression(node):
    """text() outside a statement sink: a default value / index predicate.

    Literal-only and statement-free; if it is later handed to execute() through
    a variable, that execute() is non-literal and rejected on its own.
    """
    line = node.lineno
    if (len(node.args) != 1 or node.keywords
            or not isinstance(node.args[0], ast.Constant)
            or not isinstance(node.args[0].value, str)):
        return [f"line {line}: text() with non-literal SQL cannot be proven safe"]
    if _SQL_FORBIDDEN.search(node.args[0].value) or ";" in node.args[0].value:
        return [f"line {line}: text() expression contains a statement keyword"]
    return []


def check_source(source, filename="<migration>"):
    """Return (revision, classification, problems) for one forward migration."""
    tree = ast.parse(source, filename=filename)
    revision = _module_literal(tree, "revision")
    declared = _module_literal(tree, "expand_contract")
    if not isinstance(revision, str) or not revision:
        return None, None, ["revision is not a module-level string literal"]
    if not any(isinstance(n, ast.FunctionDef) and n.name == "upgrade"
               for n in tree.body):
        return revision, None, ["no module-level def upgrade()"]
    if declared == CONTRACT:
        reason = _module_literal(tree, "expand_contract_reason")
        if not isinstance(reason, str) or len(reason.strip()) < 20:
            return revision, CONTRACT, [
                'expand_contract = "contract" requires a literal '
                "expand_contract_reason (>= 20 chars) explaining the sequencing"]
        return revision, CONTRACT, []
    if declared != EXPAND:
        return revision, None, [
            'forward migration must declare expand_contract = "expand" or '
            '"contract" as a module-level string literal']
    scope = _Scope()
    scope.visit(tree)
    return revision, EXPAND, scope.problems


def scan(versions_dir=VERSIONS_DIR, baseline=None):
    """Check every migration file. Returns {"violations": {...}, "classes": {...}}."""
    baseline = HISTORICAL_BASELINE if baseline is None else baseline
    violations, classes, seen = {}, {}, set()
    for path in sorted(Path(versions_dir).glob("*.py")):
        raw = path.read_bytes()
        source = raw.decode("utf-8")
        tree = ast.parse(source, filename=str(path))
        revision = _module_literal(tree, "revision")
        if isinstance(revision, str) and revision in baseline:
            seen.add(revision)
            if normalized_digest(raw) != baseline[revision]:
                violations[path.name] = [
                    "historical (pre-R6) migration was modified; shipped history "
                    "is immutable — add a new migration instead"]
            classes[revision] = "historical"
            continue
        revision, classification, problems = check_source(source, str(path))
        if problems:
            violations[path.name] = problems
        if revision:
            classes[revision] = classification
    missing = sorted(set(baseline) - seen)
    if missing:
        violations["<baseline>"] = [f"historical revision(s) missing: {missing}"]
    return {"violations": violations, "classes": classes}


def contract_revisions(versions_dir=VERSIONS_DIR):
    """Forward revisions declared non-blue/green-compatible (for deploy control)."""
    return sorted(rev for rev, cls in scan(versions_dir)["classes"].items()
                  if cls == CONTRACT)


def main(argv=None):
    result = scan()
    for name, problems in sorted(result["violations"].items()):
        for problem in problems:
            print(f"{name}: {problem}")
    contracts = sorted(r for r, c in result["classes"].items() if c == CONTRACT)
    if contracts:
        print(f"contract (no dual-revision overlap) revisions: {contracts}")
    if result["violations"]:
        print("expand/contract gate: FAIL")
        return 1
    print(f"expand/contract gate: PASS ({len(result['classes'])} revisions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
