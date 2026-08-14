import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql

from app.extensions import db
from app.models import PumpCheckComparison, PumpCheckComparisonRequest


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "fa1b2c3d4e5f_add_pump_check_comparisons.py"
)
COMPARISON = "pump_check_comparison"
REQUEST = "pump_check_comparison_request"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "pump_check_comparison_migration", MIGRATION
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _legacy_engine(tmp_path, name="comparison.db"):
    engine = sa.create_engine(f"sqlite:///{tmp_path / name}")
    try:
        with engine.begin() as connection:
            connection.execute(sa.text("""
                CREATE TABLE user (
                    id INTEGER PRIMARY KEY
                )
            """))
            connection.execute(sa.text("""
                CREATE TABLE pump_check (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    image_key VARCHAR(300),
                    date_key VARCHAR(10),
                    created_at DATETIME,
                    CONSTRAINT fk_pump_check_user FOREIGN KEY(user_id)
                        REFERENCES user(id) ON DELETE CASCADE
                )
            """))
            yield connection
    finally:
        engine.dispose()


def _run(connection, operation):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        operation()


def _run_upgrade(connection):
    _run(connection, _load_migration().upgrade)


def _type_contract(column_type):
    for type_class in (sa.JSON, sa.String, sa.Integer, sa.DateTime):
        if isinstance(column_type, type_class):
            return type_class
    return type(column_type)


def _expected_columns(model):
    return {
        column.name: (
            _type_contract(column.type),
            getattr(column.type, "length", None),
            column.nullable,
        )
        for column in model.__table__.columns
    }


def _inspected_columns(inspector, table):
    return {
        item["name"]: (
            _type_contract(item["type"]),
            getattr(item["type"], "length", None),
            item["nullable"],
        )
        for item in inspector.get_columns(table)
    }


def _assert_expected_constraints(inspector):
    assert _inspected_columns(inspector, COMPARISON) == _expected_columns(
        PumpCheckComparison
    )
    assert _inspected_columns(inspector, REQUEST) == _expected_columns(
        PumpCheckComparisonRequest
    )

    uniques = {
        table: {
            item["name"]: tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table)
        }
        for table in (COMPARISON, REQUEST)
    }
    assert uniques == {
        COMPARISON: {
            "uq_pump_comparison_pair_version": (
                "user_id",
                "baseline_pump_check_id",
                "current_pump_check_id",
                "analysis_version",
            ),
            "uq_pump_comparison_user_public_id": ("user_id", "public_id"),
        },
        REQUEST: {
            "uq_pump_comparison_request_user_key": ("user_id", "idempotency_key")
        },
    }

    indexes = {
        table: {
            item["name"]: (tuple(item["column_names"]), bool(item["unique"]))
            for item in inspector.get_indexes(table)
        }
        for table in (COMPARISON, REQUEST)
    }
    assert indexes == {
        COMPARISON: {
            "ix_pump_check_comparison_user_id": (("user_id",), False),
            "ix_pump_check_comparison_baseline_pump_check_id": (
                ("baseline_pump_check_id",),
                False,
            ),
            "ix_pump_check_comparison_current_pump_check_id": (
                ("current_pump_check_id",),
                False,
            ),
        },
        REQUEST: {
            "ix_pump_check_comparison_request_user_id": (("user_id",), False),
            "ix_pump_check_comparison_request_comparison_id": (
                ("comparison_id",),
                False,
            ),
        },
    }

    checks = {
        item["name"] for item in inspector.get_check_constraints(COMPARISON)
    }
    assert checks == {
        "ck_pump_comparison_distinct_sources",
        "ck_pump_comparison_status",
        "ck_pump_comparison_comparability",
        "ck_pump_comparison_terminal_fields",
    }

    foreign_keys = {
        table: {
            (
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
                item["options"].get("ondelete"),
            )
            for item in inspector.get_foreign_keys(table)
        }
        for table in (COMPARISON, REQUEST)
    }
    assert foreign_keys == {
        COMPARISON: {
            (("user_id",), "user", ("id",), "CASCADE"),
            (("baseline_pump_check_id",), "pump_check", ("id",), "CASCADE"),
            (("current_pump_check_id",), "pump_check", ("id",), "CASCADE"),
        },
        REQUEST: {
            (("user_id",), "user", ("id",), "CASCADE"),
            (("comparison_id",), COMPARISON, ("id",), "CASCADE"),
        },
    }


class _ColumnInspector:
    def __init__(self, dialect_name, type_overrides=None, default_overrides=None):
        self.bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )
        json_type = (
            postgresql.JSONB()
            if dialect_name == "postgresql"
            else sa.JSON()
        )
        self._types = {
            "id": sa.Integer(),
            "user_id": sa.Integer(),
            "baseline_pump_check_id": sa.Integer(),
            "current_pump_check_id": sa.Integer(),
            "public_id": sa.String(24),
            "status": sa.String(20),
            "comparability": sa.String(20),
            "analysis": json_type,
            "analysis_version": sa.String(50),
            "analysis_started_at": sa.DateTime(),
            "analysis_attempt": sa.Integer(),
            "analysis_failure_kind": sa.String(24),
            "created_at": sa.DateTime(),
        }
        self._types.update(type_overrides or {})
        self._defaults = {
            "status": (
                "('pending'::character varying)"
                if dialect_name == "postgresql"
                else "'pending'"
            ),
            "analysis_attempt": (
                "('0'::integer)"
                if dialect_name == "postgresql"
                else "'0'"
            ),
        }
        self._defaults.update(default_overrides or {})

    def get_columns(self, table):
        assert table == COMPARISON
        nullable = {
            "comparability",
            "analysis",
            "analysis_started_at",
            "analysis_failure_kind",
        }
        return [
            {
                "name": name,
                "type": column_type,
                "nullable": name in nullable,
                "default": self._defaults.get(name),
            }
            for name, column_type in self._types.items()
        ]

    @staticmethod
    def get_pk_constraint(table):
        assert table == COMPARISON
        return {"constrained_columns": ["id"]}


class _CheckInspector:
    def __init__(self, checks):
        self._checks = checks

    def get_check_constraints(self, table):
        assert table == COMPARISON
        return [
            {"name": name, "sqltext": sqltext}
            for name, sqltext in self._checks.items()
        ]


def _create_wrong_check_comparison(connection):
    connection.execute(sa.text("""
        CREATE TABLE pump_check_comparison (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            baseline_pump_check_id INTEGER NOT NULL,
            current_pump_check_id INTEGER NOT NULL,
            public_id VARCHAR(24) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending' NOT NULL,
            comparability VARCHAR(20),
            analysis JSON,
            analysis_version VARCHAR(50) NOT NULL,
            analysis_started_at DATETIME,
            analysis_attempt INTEGER DEFAULT '0' NOT NULL,
            analysis_failure_kind VARCHAR(24),
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_pump_comparison_pair_version UNIQUE (user_id, baseline_pump_check_id, current_pump_check_id, analysis_version),
            CONSTRAINT uq_pump_comparison_user_public_id UNIQUE (user_id, public_id),
            CONSTRAINT ck_pump_comparison_distinct_sources
                CHECK (baseline_pump_check_id <> current_pump_check_id),
            CONSTRAINT ck_pump_comparison_status
                CHECK (status IN ('pending', 'analyzing', 'completed',
                                  'failed', 'unexpected')),
            CONSTRAINT ck_pump_comparison_comparability
                CHECK (
                    comparability IS NULL OR comparability IN (
                        'comparable', 'limited', 'not_comparable'
                    )
                ),
            CONSTRAINT ck_pump_comparison_terminal_fields
                CHECK (
                    (status = 'completed'
                     AND analysis IS NOT NULL
                     AND comparability IS NOT NULL)
                    OR
                    (status <> 'completed'
                     AND analysis IS NULL
                     AND comparability IS NULL)
                ),
            FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE,
            FOREIGN KEY(baseline_pump_check_id) REFERENCES pump_check(id) ON DELETE CASCADE,
            FOREIGN KEY(current_pump_check_id) REFERENCES pump_check(id) ON DELETE CASCADE
        )
    """))


def test_upgrade_creates_both_tables_on_legacy_schema(tmp_path):
    with _legacy_engine(tmp_path) as connection:
        _run_upgrade(connection)
        inspector = sa.inspect(connection)
        tables = set(inspector.get_table_names())
        assert {COMPARISON, REQUEST} <= tables
        _assert_expected_constraints(inspector)


def test_upgrade_accepts_compatible_create_all_and_is_idempotent(app):
    with app.app_context():
        with db.engine.begin() as connection:
            _run_upgrade(connection)
            _run_upgrade(connection)
            _assert_expected_constraints(sa.inspect(connection))


def test_upgrade_rejects_incompatible_partial_comparison_table(tmp_path):
    with _legacy_engine(tmp_path, "partial-comparison.db") as connection:
        connection.execute(sa.text(
            "CREATE TABLE pump_check_comparison (id INTEGER PRIMARY KEY)"
        ))
        with pytest.raises(
            RuntimeError, match="incompatible pump_check_comparison schema"
        ):
            _run_upgrade(connection)


def test_upgrade_rejects_incompatible_partial_request_table(tmp_path):
    with _legacy_engine(tmp_path, "partial-request.db") as connection:
        migration = _load_migration()
        _run(connection, migration.upgrade)
        connection.execute(sa.text("DROP TABLE pump_check_comparison_request"))
        connection.execute(sa.text(
            "CREATE TABLE pump_check_comparison_request (id INTEGER PRIMARY KEY)"
        ))
        with pytest.raises(
            RuntimeError, match="incompatible pump_check_comparison_request schema"
        ):
            _run(connection, migration.upgrade)


def test_upgrade_rejects_correctly_named_but_wrong_check_sql(tmp_path):
    with _legacy_engine(tmp_path, "wrong-check.db") as connection:
        _create_wrong_check_comparison(connection)
        with pytest.raises(
            RuntimeError,
            match="incompatible pump_check_comparison schema: "
                  "check ck_pump_comparison_status has wrong SQL",
        ):
            _run_upgrade(connection)


def test_check_verifier_accepts_postgresql_reflection_forms():
    migration = _load_migration()
    inspector = _CheckInspector({
        "ck_pump_comparison_distinct_sources": (
            "((baseline_pump_check_id <> current_pump_check_id))"
        ),
        "ck_pump_comparison_status": (
            "((status)::text = ANY "
            "((ARRAY['pending'::character varying, "
            "'analyzing'::character varying, "
            "'completed'::character varying, "
            "'failed'::character varying])::text[]))"
        ),
        "ck_pump_comparison_comparability": (
            "((comparability IS NULL) OR "
            "((comparability)::text = ANY "
            "((ARRAY['comparable'::character varying, "
            "'limited'::character varying, "
            "'not_comparable'::character varying])::text[])))"
        ),
        "ck_pump_comparison_terminal_fields": (
            "(((status)::text = 'completed'::text) "
            "AND (analysis IS NOT NULL) "
            "AND (comparability IS NOT NULL)) OR "
            "(((status)::text <> 'completed'::text) "
            "AND (analysis IS NULL) "
            "AND (comparability IS NULL))"
        ),
    })

    migration._verify_checks(inspector, COMPARISON)


def test_check_verifier_rejects_wrong_terminal_boolean_grouping():
    migration = _load_migration()
    inspector = _CheckInspector({
        "ck_pump_comparison_distinct_sources": (
            "baseline_pump_check_id <> current_pump_check_id"
        ),
        "ck_pump_comparison_status": (
            "status IN ('pending', 'analyzing', 'completed', 'failed')"
        ),
        "ck_pump_comparison_comparability": (
            "comparability IS NULL OR comparability IN "
            "('comparable', 'limited', 'not_comparable')"
        ),
        "ck_pump_comparison_terminal_fields": (
            "status = 'completed' AND "
            "(analysis IS NOT NULL AND comparability IS NOT NULL OR "
            "status <> 'completed') AND "
            "analysis IS NULL AND comparability IS NULL"
        ),
    })

    with pytest.raises(
        RuntimeError,
        match="check ck_pump_comparison_terminal_fields has wrong SQL",
    ):
        migration._verify_checks(inspector, COMPARISON)


def test_check_verifier_rejects_case_changed_allowed_literal():
    migration = _load_migration()
    inspector = _CheckInspector({
        "ck_pump_comparison_distinct_sources": (
            "baseline_pump_check_id <> current_pump_check_id"
        ),
        "ck_pump_comparison_status": (
            "status IN ('PENDING', 'analyzing', 'completed', 'failed')"
        ),
        "ck_pump_comparison_comparability": (
            "comparability IS NULL OR comparability IN "
            "('comparable', 'limited', 'not_comparable')"
        ),
        "ck_pump_comparison_terminal_fields": (
            "(status = 'completed' AND analysis IS NOT NULL AND "
            "comparability IS NOT NULL) OR "
            "(status <> 'completed' AND analysis IS NULL AND "
            "comparability IS NULL)"
        ),
    })

    with pytest.raises(
        RuntimeError,
        match="check ck_pump_comparison_status has wrong SQL",
    ):
        migration._verify_checks(inspector, COMPARISON)


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql"])
def test_column_verifier_accepts_required_dialect_types_and_default_forms(
        dialect_name):
    migration = _load_migration()
    migration._verify_columns(
        _ColumnInspector(dialect_name), COMPARISON)


@pytest.mark.parametrize(
    ("column_name", "wrong_type"),
    [
        ("analysis", postgresql.JSON()),
        ("analysis_attempt", sa.BigInteger()),
    ],
)
def test_postgresql_column_verifier_rejects_json_and_broad_integer_subtypes(
        column_name, wrong_type):
    migration = _load_migration()
    inspector = _ColumnInspector(
        "postgresql", type_overrides={column_name: wrong_type})

    with pytest.raises(
        RuntimeError,
        match=f"incompatible pump_check_comparison schema: "
              f"column {column_name} has wrong type",
    ):
        migration._verify_columns(inspector, COMPARISON)


@pytest.mark.parametrize(
    ("column_name", "wrong_default"),
    [
        ("status", None),
        ("status", "'queued'::character varying"),
        ("status", "'PENDING'::character varying"),
        ("analysis_attempt", None),
        ("analysis_attempt", "1"),
    ],
)
def test_column_verifier_rejects_missing_or_wrong_server_defaults(
        column_name, wrong_default):
    migration = _load_migration()
    inspector = _ColumnInspector(
        "postgresql",
        default_overrides={column_name: wrong_default},
    )

    with pytest.raises(
        RuntimeError,
        match=f"incompatible pump_check_comparison schema: "
              f"column {column_name} has wrong server default",
    ):
        migration._verify_columns(inspector, COMPARISON)


def test_downgrade_preserves_pump_check_sources(tmp_path):
    with _legacy_engine(tmp_path, "downgrade.db") as connection:
        migration = _load_migration()
        _run(connection, migration.upgrade)
        _run(connection, migration.downgrade)
        tables = set(sa.inspect(connection).get_table_names())

    assert "pump_check" in tables
    assert "user" in tables
    assert COMPARISON not in tables
    assert REQUEST not in tables


def test_migration_analysis_type_compiles_as_jsonb_on_postgresql():
    migration = _load_migration()
    assert str(migration._ANALYSIS_TYPE.compile(
        dialect=postgresql.dialect()
    )) == "JSONB"
