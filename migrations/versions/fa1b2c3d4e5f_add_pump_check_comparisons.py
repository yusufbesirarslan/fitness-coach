"""Add canonical Pump Check comparison result and request-ledger tables.

Revision ID: fa1b2c3d4e5f
Revises: e9f0a1b2c3d4

The repository boot path can run db.create_all() before Alembic. This revision
therefore verifies compatible existing tables, creates absent tables and
indexes, and fails closed instead of accepting an incomplete partial schema.
"""
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
        "ck_pump_comparison_distinct_sources",
        "ck_pump_comparison_status",
        "ck_pump_comparison_comparability",
        "ck_pump_comparison_terminal_fields",
    },
    _REQUEST: set(),
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


def _verify_columns(inspector, table):
    columns = {
        item["name"]: item for item in inspector.get_columns(table)
    }
    missing = set(_COLUMN_SPECS[table]) - set(columns)
    if missing:
        _incompatible(table, f"missing columns {sorted(missing)}")

    for name, (type_class, length, nullable) in _COLUMN_SPECS[table].items():
        actual = columns[name]
        actual_type = actual["type"]
        if not isinstance(actual_type, type_class):
            _incompatible(table, f"column {name} has wrong type {actual_type}")
        if length is not None and getattr(actual_type, "length", None) != length:
            _incompatible(table, f"column {name} has wrong length")
        if bool(actual.get("nullable")) != nullable:
            _incompatible(table, f"column {name} has wrong nullability")

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


def _verify_checks(inspector, table):
    checks = {
        item["name"] for item in inspector.get_check_constraints(table)
        if item.get("name")
    }
    missing = _REQUIRED_CHECKS[table] - checks
    if missing:
        _incompatible(table, f"missing checks {sorted(missing)}")


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
