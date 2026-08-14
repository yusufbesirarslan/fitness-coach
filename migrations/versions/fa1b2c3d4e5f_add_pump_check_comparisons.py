"""Add canonical Pump Check comparison result and request-ledger tables.

Revision ID: fa1b2c3d4e5f
Revises: e9f0a1b2c3d4

The repository boot path can run db.create_all() before Alembic. This revision
therefore verifies compatible existing tables, creates absent tables and
indexes, and fails closed instead of accepting an incomplete partial schema.
"""
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "fa1b2c3d4e5f"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


_COMPARISON = "pump_check_comparison"
_REQUEST = "pump_check_comparison_request"
_ANALYSIS_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_STATUS_CHECK_SQL = (
    "status IN ('pending', 'analyzing', 'completed', 'failed')"
)
_COMPARABILITY_CHECK_SQL = (
    "comparability IS NULL OR comparability IN "
    "('comparable', 'limited', 'not_comparable')"
)
_TERMINAL_COHERENCE_SQL = (
    "(status = 'completed' AND analysis IS NOT NULL AND "
    "comparability IS NOT NULL) OR (status <> 'completed' AND "
    "analysis IS NULL AND comparability IS NULL)"
)

_COLUMN_SPECS = {
    _COMPARISON: {
        "id": (sa.Integer, None, False),
        "user_id": (sa.Integer, None, False),
        "baseline_pump_check_id": (sa.Integer, None, False),
        "current_pump_check_id": (sa.Integer, None, False),
        "public_id": (sa.String, 24, False),
        "status": (sa.String, 20, False),
        "comparability": (sa.String, 20, True),
        "analysis": (sa.JSON, None, True),
        "analysis_version": (sa.String, 50, False),
        "analysis_started_at": (sa.DateTime, None, True),
        "analysis_attempt": (sa.Integer, None, False),
        "analysis_failure_kind": (sa.String, 24, True),
        "created_at": (sa.DateTime, None, False),
    },
    _REQUEST: {
        "id": (sa.Integer, None, False),
        "user_id": (sa.Integer, None, False),
        "idempotency_key": (sa.String, 64, False),
        "fingerprint": (sa.String, 64, False),
        "comparison_id": (sa.Integer, None, False),
        "created_at": (sa.DateTime, None, False),
    },
}

_REQUIRED_UNIQUES = {
    _COMPARISON: {
        "uq_pump_comparison_pair_version": (
            "user_id",
            "baseline_pump_check_id",
            "current_pump_check_id",
            "analysis_version",
        ),
        "uq_pump_comparison_user_public_id": ("user_id", "public_id"),
    },
    _REQUEST: {
        "uq_pump_comparison_request_user_key": (
            "user_id", "idempotency_key")
    },
}

_REQUIRED_FOREIGN_KEYS = {
    _COMPARISON: {
        (("user_id",), "user", ("id",), "CASCADE"),
        (("baseline_pump_check_id",), "pump_check", ("id",), "CASCADE"),
        (("current_pump_check_id",), "pump_check", ("id",), "CASCADE"),
    },
    _REQUEST: {
        (("user_id",), "user", ("id",), "CASCADE"),
        (("comparison_id",), _COMPARISON, ("id",), "CASCADE"),
    },
}

_REQUIRED_CHECKS = {
    _COMPARISON: {
        "ck_pump_comparison_distinct_sources": (
            "baseline_pump_check_id <> current_pump_check_id"),
        "ck_pump_comparison_status": _STATUS_CHECK_SQL,
        "ck_pump_comparison_comparability": _COMPARABILITY_CHECK_SQL,
        "ck_pump_comparison_terminal_fields": _TERMINAL_COHERENCE_SQL,
    },
    _REQUEST: {},
}

_REQUIRED_DEFAULTS = {
    _COMPARISON: {
        "status": "pending",
        "analysis_attempt": "0",
    },
    _REQUEST: {},
}

_INDEXES = {
    _COMPARISON: (
        ("ix_pump_check_comparison_user_id", ("user_id",), False),
        (
            "ix_pump_check_comparison_baseline_pump_check_id",
            ("baseline_pump_check_id",),
            False,
        ),
        (
            "ix_pump_check_comparison_current_pump_check_id",
            ("current_pump_check_id",),
            False,
        ),
    ),
    _REQUEST: (
        ("ix_pump_check_comparison_request_user_id", ("user_id",), False),
        (
            "ix_pump_check_comparison_request_comparison_id",
            ("comparison_id",),
            False,
        ),
    ),
}


def _create_comparison_table():
    op.create_table(
        _COMPARISON,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("baseline_pump_check_id", sa.Integer(), nullable=False),
        sa.Column("current_pump_check_id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=24), nullable=False),
        sa.Column(
            "status", sa.String(length=20),
            server_default="pending", nullable=False),
        sa.Column("comparability", sa.String(length=20), nullable=True),
        sa.Column("analysis", _ANALYSIS_TYPE, nullable=True),
        sa.Column("analysis_version", sa.String(length=50), nullable=False),
        sa.Column("analysis_started_at", sa.DateTime(), nullable=True),
        sa.Column(
            "analysis_attempt", sa.Integer(),
            server_default="0", nullable=False),
        sa.Column(
            "analysis_failure_kind", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "baseline_pump_check_id <> current_pump_check_id",
            name="ck_pump_comparison_distinct_sources"),
        sa.CheckConstraint(
            _STATUS_CHECK_SQL, name="ck_pump_comparison_status"),
        sa.CheckConstraint(
            _COMPARABILITY_CHECK_SQL,
            name="ck_pump_comparison_comparability"),
        sa.CheckConstraint(
            _TERMINAL_COHERENCE_SQL,
            name="ck_pump_comparison_terminal_fields"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["baseline_pump_check_id"], ["pump_check.id"],
            ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_pump_check_id"], ["pump_check.id"],
            ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "baseline_pump_check_id",
            "current_pump_check_id", "analysis_version",
            name="uq_pump_comparison_pair_version"),
        sa.UniqueConstraint(
            "user_id", "public_id",
            name="uq_pump_comparison_user_public_id"),
    )


def _create_request_table():
    op.create_table(
        _REQUEST,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("comparison_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["comparison_id"], [_COMPARISON + ".id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "idempotency_key",
            name="uq_pump_comparison_request_user_key"),
    )


def _incompatible(table, detail):
    raise RuntimeError(
        f"incompatible {table} schema: {detail}. "
        "Refusing an incomplete Pump Check comparison invariant.")


def _dialect_name(inspector):
    bind = getattr(inspector, "bind", None)
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower()


def _type_is_compatible(name, actual_type, expected_type, dialect_name):
    if name == "analysis":
        if dialect_name == "postgresql":
            return isinstance(actual_type, postgresql.JSONB)
        return (
            isinstance(actual_type, sa.JSON)
            and not isinstance(actual_type, postgresql.JSONB)
        )
    if expected_type is sa.Integer:
        return getattr(actual_type, "__visit_name__", "").lower() == "integer"
    if expected_type is sa.String:
        return (
            isinstance(actual_type, sa.String)
            and getattr(actual_type, "__visit_name__", "").lower()
            in {"string", "varchar"}
        )
    if expected_type is sa.DateTime:
        return getattr(actual_type, "__visit_name__", "").lower() == "datetime"
    return type(actual_type) is expected_type


_DEFAULT_CAST_RE = re.compile(
    r"::\s*(?:character\s+varying|varchar|text|integer|int4)\b",
    re.IGNORECASE,
)


def _strip_outer_parentheses(value):
    value = value.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        wraps_whole_value = True
        in_quote = False
        for index, character in enumerate(value):
            if character == "'":
                in_quote = not in_quote
            elif not in_quote:
                if character == "(":
                    depth += 1
                elif character == ")":
                    depth -= 1
                    if depth == 0 and index != len(value) - 1:
                        wraps_whole_value = False
                        break
        if not wraps_whole_value or depth != 0:
            break
        value = value[1:-1].strip()
    return value


def _normalize_server_default(value):
    if value is None:
        return None
    normalized = _DEFAULT_CAST_RE.sub("", str(value).strip())
    normalized = _strip_outer_parentheses(normalized)
    if (
        len(normalized) >= 2
        and normalized.startswith("'")
        and normalized.endswith("'")
    ):
        normalized = normalized[1:-1].replace("''", "'")
    return normalized.strip()


def _verify_columns(inspector, table):
    columns = {
        item["name"]: item for item in inspector.get_columns(table)
    }
    missing = set(_COLUMN_SPECS[table]) - set(columns)
    if missing:
        _incompatible(table, f"missing columns {sorted(missing)}")

    dialect_name = _dialect_name(inspector)
    for name, (type_class, length, nullable) in _COLUMN_SPECS[table].items():
        actual = columns[name]
        actual_type = actual["type"]
        if not _type_is_compatible(
            name, actual_type, type_class, dialect_name
        ):
            _incompatible(table, f"column {name} has wrong type {actual_type}")
        if length is not None and getattr(actual_type, "length", None) != length:
            _incompatible(table, f"column {name} has wrong length")
        if bool(actual.get("nullable")) != nullable:
            _incompatible(table, f"column {name} has wrong nullability")
        if name in _REQUIRED_DEFAULTS[table]:
            expected_default = _REQUIRED_DEFAULTS[table][name]
            actual_default = _normalize_server_default(actual.get("default"))
            if actual_default != expected_default:
                _incompatible(
                    table,
                    f"column {name} has wrong server default "
                    f"{actual.get('default')!r}",
                )

    primary_key = tuple(
        inspector.get_pk_constraint(table).get("constrained_columns") or ())
    if primary_key != ("id",):
        _incompatible(table, f"wrong primary key {primary_key}")


def _verify_uniques(inspector, table):
    uniques = {
        item["name"]: tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }
    for name, expected_columns in _REQUIRED_UNIQUES[table].items():
        if name not in uniques:
            _incompatible(table, f"missing unique constraint {name}")
        if uniques[name] != expected_columns:
            _incompatible(table, f"unique {name} has wrong columns")


def _verify_foreign_keys(inspector, table):
    foreign_keys = {
        (
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
            str((item.get("options") or {}).get("ondelete") or "").upper(),
        )
        for item in inspector.get_foreign_keys(table)
    }
    missing = _REQUIRED_FOREIGN_KEYS[table] - foreign_keys
    if missing:
        _incompatible(table, f"missing foreign keys {sorted(missing)}")


_CHECK_CAST_RE = re.compile(
    r"::\s*(?:character\s+varying|varchar|text|integer|int4)"
    r"(?:\s*\[\s*\])?",
    re.IGNORECASE,
)
_ANY_ARRAY_RE = re.compile(
    r"=\s*any\s*\(\s*\(?\s*array\s*\[([^\]]*)\]"
    r"\s*\)?\s*\)",
    re.IGNORECASE,
)
_IDENTIFIER_PARENS_RE = re.compile(
    r"\(\s*([a-z_][a-z0-9_.]*)\s*\)",
    re.IGNORECASE,
)
_SQL_LITERAL = r"'(?:''|[^'])*'"
_SQL_IDENTIFIER = r"[a-z_][a-z0-9_.]*"
_SQL_IN_LIST = (
    rf"\(\s*{_SQL_LITERAL}"
    rf"(?:\s*,\s*{_SQL_LITERAL})*\s*\)"
)
_ATOMIC_PARENS_RE = re.compile(
    rf"\(\s*("
    rf"(?:{_SQL_IDENTIFIER}\s+is\s+(?:not\s+)?null)"
    rf"|(?:{_SQL_IDENTIFIER}\s*(?:=|<>)\s*"
    rf"(?:{_SQL_IDENTIFIER}|{_SQL_LITERAL}))"
    rf"|(?:{_SQL_IDENTIFIER}\s+in\s*{_SQL_IN_LIST})"
    rf")\s*\)",
    re.IGNORECASE,
)


def _normalize_check_sql(value):
    if value is None:
        return None
    normalized = str(value).strip().replace('"', "")
    parts = re.split(r"('(?:''|[^'])*')", normalized)
    normalized = "".join(
        part if index % 2 else part.lower()
        for index, part in enumerate(parts)
    )
    if normalized.startswith("check"):
        normalized = normalized[len("check"):].strip()
    normalized = _CHECK_CAST_RE.sub("", normalized)
    normalized = _ANY_ARRAY_RE.sub(r" in (\1)", normalized)
    normalized = normalized.replace("!=", "<>")
    while True:
        previous = normalized
        normalized = _IDENTIFIER_PARENS_RE.sub(r"\1", normalized)
        normalized = _ATOMIC_PARENS_RE.sub(r"\1", normalized)
        if normalized == previous:
            break
    normalized = _strip_outer_parentheses(normalized)
    return re.sub(r"\s+", "", normalized)


def _verify_checks(inspector, table):
    checks = {
        item["name"]: item.get("sqltext")
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }
    missing = set(_REQUIRED_CHECKS[table]) - set(checks)
    if missing:
        _incompatible(table, f"missing checks {sorted(missing)}")
    for name, expected_sql in _REQUIRED_CHECKS[table].items():
        if _normalize_check_sql(checks[name]) != _normalize_check_sql(
            expected_sql
        ):
            _incompatible(table, f"check {name} has wrong SQL")


def _verify_existing(inspector, table):
    _verify_columns(inspector, table)
    _verify_uniques(inspector, table)
    _verify_foreign_keys(inspector, table)
    _verify_checks(inspector, table)


def _existing_named_objects(inspector, table):
    names = {
        item["name"] for item in inspector.get_indexes(table)
        if item.get("name")
    }
    names |= {
        item["name"] for item in inspector.get_unique_constraints(table)
        if item.get("name")
    }
    return names


def _ensure_indexes(inspector, table):
    existing_indexes = {
        item["name"]: (
            tuple(item.get("column_names") or ()),
            bool(item.get("unique")),
        )
        for item in inspector.get_indexes(table)
        if item.get("name")
    }
    existing_names = _existing_named_objects(inspector, table)
    for name, columns, unique in _INDEXES[table]:
        if name in existing_names and name not in existing_indexes:
            _incompatible(
                table, f"{name} exists but is not the required index")
        if (
            name in existing_indexes
            and existing_indexes[name] != (columns, unique)
        ):
            _incompatible(
                table, f"index {name} has wrong columns or uniqueness")
        if name not in existing_names:
            op.create_index(name, table, list(columns), unique=unique)


def upgrade():
    bind = op.get_bind()
    for table, creator in (
        (_COMPARISON, _create_comparison_table),
        (_REQUEST, _create_request_table),
    ):
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            _verify_existing(inspector, table)
        else:
            creator()
        _ensure_indexes(sa.inspect(bind), table)


def downgrade():
    bind = op.get_bind()
    for table in (_REQUEST, _COMPARISON):
        inspector = sa.inspect(bind)
        if inspector.has_table(table):
            op.drop_table(table)
