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


_SUPPORTED_DIALECTS = frozenset({"postgresql", "sqlite"})

# Reflection never returns the model-side declaration classes. Each dialect
# reports the type name its catalog actually stores, so the accepted spellings
# are listed per dialect instead of per SQLAlchemy class.
_DATETIME_VISIT_NAMES = {
    "postgresql": {"datetime", "timestamp"},
    "sqlite": {"datetime"},
}


def _dialect_name(inspector):
    bind = getattr(inspector, "bind", None)
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "") or "").lower()


def _visit_name(actual_type):
    return str(getattr(actual_type, "__visit_name__", "") or "").lower()


def _type_is_compatible(name, actual_type, expected_type, dialect_name):
    if name == "analysis":
        if dialect_name == "postgresql":
            return isinstance(actual_type, postgresql.JSONB)
        return (
            isinstance(actual_type, sa.JSON)
            and not isinstance(actual_type, postgresql.JSONB)
        )
    if expected_type is sa.Integer:
        return _visit_name(actual_type) == "integer"
    if expected_type is sa.String:
        return (
            isinstance(actual_type, sa.String)
            and _visit_name(actual_type) in {"string", "varchar"}
        )
    if expected_type is sa.DateTime:
        # A timezone-aware column is a different column: the application
        # stores naive UTC and comparing the two silently shifts instants.
        return (
            isinstance(actual_type, sa.DateTime)
            and _visit_name(actual_type) in _DATETIME_VISIT_NAMES[dialect_name]
            and not getattr(actual_type, "timezone", False)
        )
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
    if dialect_name not in _SUPPORTED_DIALECTS:
        # Falling through would drop every dialect-specific rule (notably the
        # PostgreSQL JSONB requirement) and accept an unverified schema.
        _incompatible(table, "unsupported or undetermined database dialect")

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
# PostgreSQL renders an IN list as `= ANY (ARRAY[...])`, and reflection may or
# may not keep the inner parentheses around ARRAY. The two spellings are matched
# SEPARATELY on purpose: one optional-paren pattern would let the closing paren
# of an ENCLOSING group be consumed as if it belonged to the ANY expression,
# silently unbalancing the rest of the predicate.
_ANY_ARRAY_RE = re.compile(
    r"=\s*any\s*\(\s*\(\s*array\s*\[([^\]]*)\]\s*\)\s*\)"
    r"|=\s*any\s*\(\s*array\s*\[([^\]]*)\]\s*\)",
    re.IGNORECASE,
)
_IDENTIFIER_PARENS_RE = re.compile(
    r"\(\s*([a-z_][a-z0-9_.]*)\s*\)",
    re.IGNORECASE,
)
_SQL_LITERAL = r"'(?:''|[^'])*'"
_SQL_IN_LIST = (
    rf"\(\s*{_SQL_LITERAL}"
    rf"(?:\s*,\s*{_SQL_LITERAL})*\s*\)"
)
_IN_LIST_RE = re.compile(rf"\bin\s*{_SQL_IN_LIST}", re.IGNORECASE)
_OPEN_MASK = "\x02"
_CLOSE_MASK = "\x03"
_BOOL_TOKEN_RE = re.compile(r"\(|\)|[^()\s]+")
_BOOL_KEYWORDS = ("(", ")", "and", "or")


def _rewrite_any_array(match):
    return " in (" + (match.group(1) or match.group(2)) + ")"


def _mask_in_lists(value):
    """Hide IN-list parentheses so they are not read as boolean grouping."""
    return _IN_LIST_RE.sub(
        lambda match: match.group(0)
        .replace("(", _OPEN_MASK).replace(")", _CLOSE_MASK),
        value,
    )


def _parse_disjunction(tokens, position):
    operands = []
    node, position = _parse_conjunction(tokens, position)
    operands.append(node)
    while position < len(tokens) and tokens[position] == "or":
        node, position = _parse_conjunction(tokens, position + 1)
        operands.append(node)
    if len(operands) == 1:
        return operands[0], position
    return "or(" + ",".join(operands) + ")", position


def _parse_conjunction(tokens, position):
    operands = []
    node, position = _parse_primary(tokens, position)
    operands.append(node)
    while position < len(tokens) and tokens[position] == "and":
        node, position = _parse_primary(tokens, position + 1)
        operands.append(node)
    if len(operands) == 1:
        return operands[0], position
    return "and(" + ",".join(operands) + ")", position


def _parse_primary(tokens, position):
    if position < len(tokens) and tokens[position] == "(":
        node, position = _parse_disjunction(tokens, position + 1)
        if position < len(tokens) and tokens[position] == ")":
            position += 1
        return node, position
    words = []
    while position < len(tokens) and tokens[position] not in _BOOL_KEYWORDS:
        words.append(tokens[position])
        position += 1
    return " ".join(words), position


def _canonical_boolean(value):
    """Re-express the predicate as an explicit AND/OR tree.

    Reflection drops parentheses that AND-binds-tighter-than-OR already
    implies, so a textual comparison would reject a schema that is in fact
    identical. Rebuilding the tree makes redundant grouping disappear while a
    GENUINELY different grouping still compares unequal.
    """
    tokens = _BOOL_TOKEN_RE.findall(value)
    if not tokens:
        return value
    node, position = _parse_disjunction(tokens, 0)
    if position != len(tokens):
        # Something unparsed remains: keep it so the comparison fails closed.
        return value
    return node


_QUOTED_IDENTIFIER_RE = re.compile(r'"(?:""|[^"])*"')
_BARE_IDENTIFIER_RE = re.compile(r"[a-z_][a-z0-9_]*")


def _resolve_quoted_identifier(match):
    raw = match.group(0)[1:-1].replace('""', '"')
    if _BARE_IDENTIFIER_RE.fullmatch(raw):
        # Quoting a lower-case identifier changes nothing in any dialect.
        return raw
    # Any other quoted identifier is case-sensitive and therefore a DIFFERENT
    # column: encode it so it can never collapse onto the unquoted name.
    return "qi_" + raw.encode("utf-8").hex()


def _encode_literal(part):
    content = part[1:-1].replace("''", "'")
    # Hex keeps the literal exactly distinguishable while surviving the
    # case-folding and whitespace-stripping applied to the SQL around it.
    return "'" + content.encode("utf-8").hex() + "'"


def _normalize_check_sql(value):
    if value is None:
        return None
    parts = re.split(r"('(?:''|[^'])*')", str(value).strip())
    normalized = "".join(
        _encode_literal(part)
        if index % 2
        else _QUOTED_IDENTIFIER_RE.sub(
            _resolve_quoted_identifier, part).lower()
        for index, part in enumerate(parts)
    )
    if normalized.startswith("check"):
        normalized = normalized[len("check"):].strip()
    normalized = _CHECK_CAST_RE.sub("", normalized)
    normalized = _ANY_ARRAY_RE.sub(_rewrite_any_array, normalized)
    normalized = normalized.replace("!=", "<>")
    while True:
        previous = normalized
        normalized = _IDENTIFIER_PARENS_RE.sub(r"\1", normalized)
        if normalized == previous:
            break
    normalized = _mask_in_lists(normalized)
    normalized = _strip_outer_parentheses(normalized)
    normalized = _canonical_boolean(normalized)
    normalized = normalized.replace(_OPEN_MASK, "(").replace(_CLOSE_MASK, ")")
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
