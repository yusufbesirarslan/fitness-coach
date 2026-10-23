"""Apple lifecycle adoption on real disposable PostgreSQL 16 and 18.

Run only with FITX_PG_CONCURRENCY_TEST=1 and PG_TEST_DATABASE_URL. Catalog
fault injection covers states PostgreSQL DDL itself cannot normally produce;
all other assertions use real DDL, reflection, transactions and persisted rows.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_apple_lifecycle_migration import migration, TABLES
from tests.test_mobile_training_allowance_pg import migrate_engine  # noqa: F401

pytestmark = [pytest.mark.pg_concurrency, pytest.mark.skipif(
    os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1", reason="requires disposable PostgreSQL")]


def _upgrade(conn, module=None):
    with Operations.context(MigrationContext.configure(conn)):
        (module or migration()).upgrade()


def _create(conn, tables=TABLES):
    from app.models import AppleNotificationReceipt, AppleSubscriptionReconciliation
    for model in (AppleNotificationReceipt, AppleSubscriptionReconciliation):
        if model.__tablename__ in tables:
            model.__table__.create(conn)


def _seed(conn, table):
    if table == TABLES[0]:
        conn.exec_driver_sql(f"INSERT INTO {table} (notification_uuid, environment, notification_type, "
                             "subtype, disposition, signed_at, received_at) "
                             "VALUES ('kept', 'Sandbox', 'DID_RENEW', 'kept', 'ENQUEUED', now(), now())")
    else:
        conn.exec_driver_sql(f"INSERT INTO {table} (environment, original_transaction_id, account_token, generation, "
                             "attempts, next_attempt_at, created_at, updated_at) "
                             "VALUES ('Sandbox', 'kept', 'kept', 3, 2, now(), now(), now())")


def _snapshot(conn):
    return {table: conn.exec_driver_sql(f"SELECT * FROM {table}").all()
            for table in TABLES if sa.inspect(conn).has_table(table)}


def _catalog(conn, table):
    return conn.execute(sa.text(
        "SELECT contype, convalidated, pg_catalog.pg_get_constraintdef(oid) "
        "FROM pg_catalog.pg_constraint WHERE conrelid = pg_catalog.to_regclass(:table) "
        "ORDER BY contype, conname"), {"table": table}).all()


@pytest.mark.parametrize("existing", [(), TABLES, TABLES[:1], TABLES[1:]])
def test_fresh_adoption_mixed_and_repeated_upgrade(migrate_engine, existing):
    with migrate_engine.begin() as conn:
        _create(conn, existing)
        for table in existing:
            _seed(conn, table)
        before = _snapshot(conn)
        _upgrade(conn)
        _upgrade(conn)
        for table, rows in before.items():
            assert _snapshot(conn)[table] == rows
        for table, columns, primary, check, index in migration()._contracts():
            assert sa.inspect(conn).get_pk_constraint(table)["constrained_columns"] == primary
            states = _catalog(conn, table)
            assert sorted(t for t, _, _ in states if t != "n") == ["c", "p"]
            not_null = [(valid, text) for t, valid, text in states if t == "n"]
            required = [(True, f"NOT NULL {c.name}") for c in columns() if not c.nullable]
            assert sorted(not_null) == (sorted(required) if conn.dialect.server_version_info >= (18,) else [])
            migration()._verify(conn, table, columns, primary, check, index)


ALTERATIONS = [
    "ALTER COLUMN {required} DROP NOT NULL",
    "ALTER COLUMN {optional} SET NOT NULL",
    "ALTER COLUMN {required} TYPE VARCHAR(41)",
    "ALTER COLUMN signed_at TYPE TIMESTAMP WITHOUT TIME ZONE",
    "DROP CONSTRAINT {table}_pkey",
    "DROP CONSTRAINT {table}_pkey, ADD PRIMARY KEY ({wrong_primary})",
    "DROP CONSTRAINT {check}",
    "DROP CONSTRAINT {check}, ADD CONSTRAINT {check} CHECK (environment IN ('Sandbox', 'Xcode'))",
    "DROP CONSTRAINT {check}, ADD CONSTRAINT {check} CHECK (environment IN ('Sandbox', 'Production')) NOT VALID",
    "ADD CONSTRAINT extra CHECK (true)",
    "ADD CONSTRAINT extra UNIQUE ({required})",
    "ADD CONSTRAINT extra FOREIGN KEY (environment) REFERENCES allowed_environment(environment)",
    "ADD CONSTRAINT extra EXCLUDE USING btree ({required} WITH =)",
]


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("alteration", ALTERATIONS)
def test_incompatible_adoption_preserves_schema_data_and_missing_peer(migrate_engine, table, alteration):
    if "signed_at" in alteration and table != TABLES[0]:
        alteration = alteration.replace("signed_at", "created_at")
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE allowed_environment (environment VARCHAR(10) PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO allowed_environment VALUES ('Sandbox')")
        _create(conn, (table,))
        _seed(conn, table)
        names = dict(table=table,
                     required="notification_type" if table == TABLES[0] else "last_outcome",
                     optional="subtype" if table == TABLES[0] else "account_token",
                     wrong_primary="environment" if table == TABLES[0] else "original_transaction_id",
                     check="ck_apple_notification_environment" if table == TABLES[0]
                     else "ck_apple_reconciliation_environment")
        # The request's required integer/timestamp columns cannot use the type
        # mutation above; last_outcome is suitable for a wrong VARCHAR type.
        if "DROP NOT NULL" in alteration:
            names["required"] = "notification_type" if table == TABLES[0] else "generation"
        conn.exec_driver_sql(f"ALTER TABLE {table} " + alteration.format(**names))
        before, catalog = _snapshot(conn), _catalog(conn, table)
        with pytest.raises(RuntimeError, match="incompatible Apple reconciliation schema"):
            _upgrade(conn)
        assert _snapshot(conn) == before
        assert _catalog(conn, table) == catalog
        assert not sa.inspect(conn).has_table(next(t for t in TABLES if t != table))
    # Verify committed data and the missing peer from a different transaction.
    with migrate_engine.connect() as conn:
        assert _snapshot(conn) == before


@pytest.mark.parametrize("index_ddl", [
    "DROP INDEX ix_apple_reconciliation_due",
    "DROP INDEX ix_apple_reconciliation_due; CREATE INDEX ix_apple_reconciliation_due "
    "ON apple_subscription_reconciliation(next_attempt_at, environment)",
    "CREATE INDEX extra ON apple_subscription_reconciliation(generation)",
])
def test_indexes_still_fail_closed(migrate_engine, index_ddl):
    with migrate_engine.begin() as conn:
        _create(conn)
        conn.exec_driver_sql(index_ddl)
        with pytest.raises(RuntimeError, match="incompatible Apple reconciliation schema"):
            _upgrade(conn)


@pytest.mark.parametrize("table,column", [(TABLES[0], "notification_type"), (TABLES[1], "generation")])
def test_pg18_unvalidated_not_null_rejected(migrate_engine, table, column):
    with migrate_engine.begin() as conn:
        if conn.dialect.server_version_info < (18,):
            pytest.skip("NOT NULL catalog DDL requires PostgreSQL 18")
        _create(conn, (table,))
        _seed(conn, table)
        conn.exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL")
        conn.exec_driver_sql(f"ALTER TABLE {table} ADD CONSTRAINT unvalidated NOT NULL {column} NOT VALID")
        assert any(t == "n" and v is False for t, v, _ in _catalog(conn, table))
        before = _snapshot(conn)
        with pytest.raises(RuntimeError, match="incompatible Apple reconciliation schema"):
            _upgrade(conn)
        assert _snapshot(conn) == before


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("fault", ["partial", "duplicate", "extra", "malformed", "unvalidated", "unexpected"])
def test_catalog_faults_cannot_bypass_real_column_verification(migrate_engine, monkeypatch, table, fault):
    with migrate_engine.begin() as conn:
        _create(conn)
        contract = next(c for c in migration()._contracts() if c[0] == table)
        expected = [("n", True, f"NOT NULL {c.name}") for c in contract[1]() if not c.nullable]
        rows = [r for r in _catalog(conn, table) if r[0] != "n"] + expected
        if fault == "partial":
            rows.pop()
        elif fault == "duplicate":
            rows.append(expected[0])
        elif fault == "extra":
            rows.append(("n", True, "NOT NULL unexpected_column"))
        elif fault == "malformed":
            rows[-1] = ("n", True, expected[-1][2] + " NO INHERIT")
        elif fault == "unvalidated":
            rows[-1] = ("n", False, expected[-1][2])
        else:
            rows.append(("t", True, "TRIGGER unexpected"))
        execute = conn.execute

        def catalog_fault(statement, parameters=None, *args, **kwargs):
            if str(statement).startswith("SELECT contype, convalidated,"):
                return SimpleNamespace(all=lambda: rows)
            return execute(statement, parameters, *args, **kwargs)

        monkeypatch.setattr(conn, "execute", catalog_fault)
        with pytest.raises(RuntimeError, match="incompatible Apple reconciliation schema"):
            migration()._verify(conn, *contract)


@pytest.mark.parametrize("existing", [(), TABLES, TABLES[:1], TABLES[1:]])
def test_full_alembic_chain_and_adoption(migrate_engine, existing):
    from flask import Flask
    from flask_migrate import Migrate, check, upgrade
    from app.extensions import db
    app = Flask("synthetic-apple-lifecycle-chain")
    app.config.update(SQLALCHEMY_DATABASE_URI=migrate_engine.url.render_as_string(hide_password=False),
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    Migrate(app, db, directory=str(Path(__file__).resolve().parents[1] / "migrations"))
    with app.app_context():
        try:
            upgrade(revision=migration().down_revision)
            with db.engine.begin() as conn:
                _create(conn, existing)
                for table in existing:
                    _seed(conn, table)
                before = _snapshot(conn)
            upgrade()
            upgrade()
            check()
            with db.engine.connect() as conn:
                assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == migration().revision
                for table, rows in before.items():
                    assert _snapshot(conn)[table] == rows
        finally:
            db.session.remove()
            db.engine.dispose()


def test_failed_alembic_adoption_keeps_revision_schema_and_data(migrate_engine):
    from flask import Flask
    from flask_migrate import Migrate, upgrade
    from app.extensions import db
    app = Flask("synthetic-apple-lifecycle-atomicity")
    app.config.update(SQLALCHEMY_DATABASE_URI=migrate_engine.url.render_as_string(hide_password=False),
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    Migrate(app, db, directory=str(Path(__file__).resolve().parents[1] / "migrations"))
    with app.app_context():
        try:
            upgrade(revision=migration().down_revision)
            with db.engine.begin() as conn:
                _create(conn, TABLES[1:])
                _seed(conn, TABLES[1])
                conn.exec_driver_sql(f"ALTER TABLE {TABLES[1]} ADD CONSTRAINT extra CHECK (true)")
                before = _snapshot(conn)
                catalog = _catalog(conn, TABLES[1])
            with pytest.raises((RuntimeError, SystemExit)):
                upgrade()
            with db.engine.connect() as conn:
                assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == migration().down_revision
                assert _snapshot(conn) == before
                assert _catalog(conn, TABLES[1]) == catalog
                assert not sa.inspect(conn).has_table(TABLES[0])
        finally:
            db.session.remove()
            db.engine.dispose()
