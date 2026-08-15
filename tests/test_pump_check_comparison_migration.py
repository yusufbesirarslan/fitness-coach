import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

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
    """Fake inspector returning the types real reflection produces.

    Reflection never returns the model-side declaration classes. PostgreSQL
    returns ``INTEGER`` / ``VARCHAR`` / ``postgresql.TIMESTAMP`` / ``JSONB``
    and SQLite returns ``INTEGER`` / ``VARCHAR`` / ``DATETIME`` /
    ``sqlite.JSON``. Building this fixture from ``sa.Integer`` / ``sa.String``
    / ``sa.DateTime`` would make every dialect type rule decorative: it would
    pass precisely because it does not resemble the database it claims to
    model. ``dialect_name=None`` models an inspector whose dialect cannot be
    determined at all.
    """

    def __init__(self, dialect_name, type_overrides=None, default_overrides=None):
        self.bind = (
            None
            if dialect_name is None
            else SimpleNamespace(dialect=SimpleNamespace(name=dialect_name))
        )
        is_postgresql = dialect_name == "postgresql"
        json_type = postgresql.JSONB() if is_postgresql else sqlite.JSON()
        timestamp_type = (
            postgresql.TIMESTAMP() if is_postgresql else sa.DATETIME()
        )
        self._types = {
            "id": sa.INTEGER(),
            "user_id": sa.INTEGER(),
            "baseline_pump_check_id": sa.INTEGER(),
            "current_pump_check_id": sa.INTEGER(),
            "public_id": sa.VARCHAR(24),
            "status": sa.VARCHAR(20),
            "comparability": sa.VARCHAR(20),
            "analysis": json_type,
            "analysis_version": sa.VARCHAR(50),
            "analysis_started_at": timestamp_type,
            "analysis_attempt": sa.INTEGER(),
            "analysis_failure_kind": sa.VARCHAR(24),
            "created_at": timestamp_type,
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


def _expected_check_definitions(**overrides):
    definitions = {
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
            "(status = 'completed' AND analysis IS NOT NULL AND "
            "comparability IS NOT NULL) OR (status <> 'completed' AND "
            "analysis IS NULL AND comparability IS NULL)"
        ),
    }
    definitions.update(overrides)
    return definitions


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


@pytest.mark.parametrize(
    "drifted_status_sql",
    [
        # Whitespace inside a quoted literal is a different allowed value.
        "status IN ('pend ing', 'analyzing', 'completed', 'failed')",
        # A quoted, case-sensitive PostgreSQL identifier is a different column.
        '"STATUS" IN (\'pending\', \'analyzing\', \'completed\', \'failed\')',
    ],
)
def test_check_verifier_rejects_literal_and_quoted_identifier_drift(
        drifted_status_sql):
    migration = _load_migration()
    inspector = _CheckInspector(
        _expected_check_definitions(
            ck_pump_comparison_status=drifted_status_sql))

    with pytest.raises(
        RuntimeError,
        match="check ck_pump_comparison_status has wrong SQL",
    ):
        migration._verify_checks(inspector, COMPARISON)


def test_check_verifier_accepts_meaningless_identifier_quoting():
    migration = _load_migration()
    inspector = _CheckInspector(
        _expected_check_definitions(
            ck_pump_comparison_status=(
                '"status" IN (\'pending\', \'analyzing\', '
                "'completed', 'failed')"
            )))

    migration._verify_checks(inspector, COMPARISON)


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql"])
def test_column_verifier_accepts_required_dialect_types_and_default_forms(
        dialect_name):
    migration = _load_migration()
    migration._verify_columns(
        _ColumnInspector(dialect_name), COMPARISON)


@pytest.mark.parametrize("dialect_name", [None, "mysql"])
def test_column_verifier_fails_closed_on_undetermined_dialect(dialect_name):
    # An unresolvable dialect must not fall through to the permissive branch:
    # that would silently drop the PostgreSQL JSONB requirement.
    migration = _load_migration()

    with pytest.raises(
        RuntimeError,
        match="incompatible pump_check_comparison schema: "
              "unsupported or undetermined database dialect",
    ):
        migration._verify_columns(_ColumnInspector(dialect_name), COMPARISON)


@pytest.mark.parametrize(
    ("dialect_name", "column_name", "wrong_type"),
    [
        ("postgresql", "created_at", postgresql.TIMESTAMP(timezone=True)),
        ("postgresql", "created_at", sa.DATE()),
        ("sqlite", "created_at", sa.DATE()),
        ("postgresql", "public_id", sa.TEXT()),
        ("postgresql", "analysis_attempt", sa.SMALLINT()),
    ],
)
def test_column_verifier_still_rejects_genuinely_wrong_reflected_types(
        dialect_name, column_name, wrong_type):
    migration = _load_migration()
    inspector = _ColumnInspector(
        dialect_name, type_overrides={column_name: wrong_type})

    with pytest.raises(
        RuntimeError,
        match=f"incompatible pump_check_comparison schema: "
              f"column {column_name} has wrong",
    ):
        migration._verify_columns(inspector, COMPARISON)


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


_INSERT_COMPARISON = sa.text("""
    INSERT INTO pump_check_comparison (
        user_id, baseline_pump_check_id, current_pump_check_id, public_id,
        status, comparability, analysis, analysis_version, created_at
    ) VALUES (
        :user_id, :baseline_pump_check_id, :current_pump_check_id, :public_id,
        :status, :comparability, :analysis, :analysis_version, :created_at
    )
""")

_COMPARISON_ROW = {
    "user_id": 1,
    "baseline_pump_check_id": 10,
    "current_pump_check_id": 11,
    "public_id": "A" * 24,
    "status": "pending",
    "comparability": None,
    "analysis": None,
    "analysis_version": "pump-check-comparison-analysis/v1",
    "created_at": "2026-08-14 09:00:00",
}


@contextmanager
def _enforced_comparison_schema(tmp_path, name):
    """A real database built by this migration, ready for insert probes.

    Structural assertions cannot tell an enforced constraint from a
    tautological one; only a rejected INSERT can.
    """
    with _legacy_engine(tmp_path, name) as connection:
        _run_upgrade(connection)
        connection.execute(sa.text("INSERT INTO user (id) VALUES (1), (2)"))
        connection.execute(sa.text(
            "INSERT INTO pump_check (id, user_id) VALUES (10, 1), (11, 1)"))
        yield connection


def _insert_comparison(connection, **overrides):
    # A savepoint keeps the surrounding transaction usable after a rejection,
    # so one test can probe several violations and a valid control row.
    with connection.begin_nested():
        connection.execute(
            _INSERT_COMPARISON, dict(_COMPARISON_ROW, **overrides))


def _comparison_count(connection):
    return connection.execute(
        sa.text("SELECT count(*) FROM pump_check_comparison")).scalar()


def test_pair_unique_constraint_is_directional_and_owner_scoped(tmp_path):
    with _enforced_comparison_schema(tmp_path, "pair-unique.db") as connection:
        _insert_comparison(connection)
        # (owner, B, A, v) is a different comparison than (owner, A, B, v):
        # the constraint must not canonicalize or sort the pair.
        _insert_comparison(
            connection, public_id="B" * 24,
            baseline_pump_check_id=11, current_pump_check_id=10)
        # Another owner may hold the very same directional pair.
        _insert_comparison(connection, user_id=2, public_id="C" * 24)
        # Another analysis version is another comparison.
        _insert_comparison(
            connection, public_id="D" * 24,
            analysis_version="pump-check-comparison-analysis/v2")

        with pytest.raises(IntegrityError) as duplicate:
            _insert_comparison(connection, public_id="E" * 24)

        message = str(duplicate.value)
        assert "UNIQUE constraint failed" in message
        assert "analysis_version" in message
        assert _comparison_count(connection) == 4


def test_distinct_sources_check_rejects_a_self_comparison(tmp_path):
    with _enforced_comparison_schema(
            tmp_path, "distinct-sources.db") as connection:
        with pytest.raises(IntegrityError) as violation:
            _insert_comparison(connection, current_pump_check_id=10)

        assert "ck_pump_comparison_distinct_sources" in str(violation.value)
        assert _comparison_count(connection) == 0

        _insert_comparison(connection)
        assert _comparison_count(connection) == 1


def test_terminal_coherence_check_is_enforced_on_insert(tmp_path):
    analysis = '{"summary": "ok"}'
    with _enforced_comparison_schema(tmp_path, "terminal.db") as connection:
        for label, overrides in (
            ("completed without analysis",
             {"status": "completed", "comparability": "comparable"}),
            ("completed without comparability",
             {"status": "completed", "analysis": analysis}),
            ("pending carrying analysis", {"analysis": analysis}),
            ("failed carrying comparability",
             {"status": "failed", "comparability": "limited"}),
        ):
            with pytest.raises(IntegrityError) as violation:
                _insert_comparison(connection, **overrides)
            assert (
                "ck_pump_comparison_terminal_fields" in str(violation.value)
            ), label

        assert _comparison_count(connection) == 0

        _insert_comparison(connection)
        _insert_comparison(
            connection, public_id="B" * 24,
            baseline_pump_check_id=11, current_pump_check_id=10,
            status="completed", comparability="comparable", analysis=analysis)
        assert _comparison_count(connection) == 2


def test_migration_and_model_check_sql_definitions_agree():
    # The migration keeps its own copies on purpose (a migration must not
    # import live models), but a silent divergence would make every fresh
    # database boot fail closed with "check ... has wrong SQL".
    migration = _load_migration()
    model_checks = {
        constraint.name: " ".join(str(constraint.sqltext).split())
        for constraint in PumpCheckComparison.__table__.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    migration_checks = {
        name: " ".join(sql.split())
        for name, sql in migration._REQUIRED_CHECKS[COMPARISON].items()
    }

    assert model_checks == migration_checks


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
