"""MON-02-BE-03B-01B-R2: b1e4f5a6c7d8 accepts only its own schema on PostgreSQL.

The verify-or-create path (create_all-first boot, reentrant upgrade, drifted
database) must fail closed on any pre-existing definition that differs from the
migration's DDL. Each defect below was reproduced as ACCEPTED by review R1.
A refused upgrade leaves the database exactly as it was (nothing half-applied,
nothing stamped). Disposable database only (PG_TEST_DATABASE_URL).
"""
import pytest

from tests.test_mobile_training_allowance_pg import (  # noqa: F401
    _module, _run, migrate_engine,
)

pytestmark = pytest.mark.pg_concurrency

LEDGER = "a9c1d2e3f4b5_ai_allowance_ledger.py"
OPERATIONS = "d3e4f5a6b7c8_add_training_plan_generation_operations.py"
MARKER = "b0d3e4f5a6b7_training_dispatch_boundary.py"
HEAD = "b1e4f5a6c7d8_training_allowance_association.py"
BINDING = "training_plan_generation_allowance"
STATES = "'RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED', 'UNOBSERVABLE'"
CHECK = f"CONSTRAINT ck_training_plan_generation_allowance_state CHECK (state IN ({STATES}))"
OPERATION_FK = "REFERENCES training_plan_generation_operation(id) ON DELETE CASCADE"
USER_FK = 'REFERENCES "user"(id) ON DELETE CASCADE'


def _binding(*, operation_id=f"INTEGER NOT NULL {OPERATION_FK}", user_id=f"INTEGER NOT NULL {USER_FK}",
             ref="VARCHAR(32) NOT NULL", state="VARCHAR(16) NOT NULL",
             created_at="TIMESTAMP WITHOUT TIME ZONE NOT NULL", extra="", key="PRIMARY KEY (operation_id)",
             unique="CONSTRAINT uq_training_plan_generation_allowance_ref UNIQUE (reservation_ref)",
             check=CHECK, after=(), index="(user_id, state)", unique_index=False):
    """The exact migration DDL, with one part replaced per negative control."""
    def make(conn):
        parts = [f"operation_id {operation_id}", f"user_id {user_id}", f"reservation_ref {ref}",
                 f"state {state}", f"created_at {created_at}",
                 "updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL", extra, key, unique, check]
        conn.exec_driver_sql(f"CREATE TABLE {BINDING} ({', '.join(p for p in parts if p)})")
        for statement in after:
            conn.exec_driver_sql(statement)
        if index:
            conn.exec_driver_sql(
                f"CREATE {'UNIQUE ' if unique_index else ''}INDEX ix_training_plan_generation_allowance_owner "
                f"ON {BINDING} {index}")
    return make


def _sql(*statements):
    def make(conn):
        for statement in statements:
            conn.exec_driver_sql(statement)
    return make


def _ledger(*reservation_sql):
    """The original a9c1 ledger tables, built BEFORE a9c1 runs (create_all-first shape)."""
    def make(conn):
        tables = {t.name: t for t in _module(LEDGER)._tables()}
        tables["ai_starter_grant"].create(conn)
        tables["ai_allowance_bucket"].create(conn)
        tables["ai_usage_reservation"].c.provider_billed.nullable = True
        tables["ai_usage_reservation"].create(conn)
        conn.exec_driver_sql(
            "CREATE INDEX ix_ai_usage_reservation_open ON ai_usage_reservation (user_id, state, expires_at)")
        for statement in reservation_sql:
            conn.exec_driver_sql(statement)
    return make


ALTER = f"ALTER TABLE {BINDING} "
RESERVATION = "ALTER TABLE ai_usage_reservation "
OPERATION = "ALTER TABLE training_plan_generation_operation "

# case -> (pre-existing schema made after b0d3, or ("ledger", maker) before a9c1;
#          None = accepted, else the refusal text b1e4 must raise)
CASES = {
    # 16. valid existing schemas (create_all-first / reentrant)
    "exact_binding": (_binding(), None),
    "exact_binding_without_owner_index": (_binding(index=None), None),
    "exact_later_reservation_columns": (_sql(
        RESERVATION + "ADD COLUMN billing_evidence VARCHAR(24)",
        RESERVATION + "ALTER COLUMN provider_billed DROP NOT NULL",
        OPERATION + "ADD COLUMN allowance_metered BOOLEAN"), None),
    # 1-4. state CHECK
    "state_check_missing": (_binding(check=""), "binding has incompatible state check"),
    "state_check_not_valid": (_binding(check="", after=(
        ALTER + f"ADD {CHECK} NOT VALID",)), "binding has incompatible state check"),
    "state_check_true": (_binding(
        check="CONSTRAINT ck_training_plan_generation_allowance_state CHECK (true)"),
        "binding has incompatible state check"),
    "state_check_missing_value": (_binding(
        check="CONSTRAINT ck_training_plan_generation_allowance_state CHECK "
              "(state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED'))"),
        "binding has incompatible state check"),
    "state_check_extra_value": (_binding(
        check=f"CONSTRAINT ck_training_plan_generation_allowance_state CHECK (state IN ({STATES}, 'ANY'))"),
        "binding has incompatible state check"),
    "state_check_renamed": (_binding(
        check=f"CONSTRAINT ck_binding_state CHECK (state IN ({STATES}))"),
        "binding has incompatible state check"),
    # 5-8. column types and lengths
    "ref_varchar8": (_binding(ref="VARCHAR(8) NOT NULL"), "binding has incompatible columns"),
    "ref_integer": (_binding(ref="INTEGER NOT NULL"), "binding has incompatible columns"),
    "ref_char32": (_binding(ref="CHAR(32) NOT NULL"), "binding has incompatible columns"),
    "ref_text": (_binding(ref="TEXT NOT NULL"), "binding has incompatible columns"),
    "state_varchar10": (_binding(state="VARCHAR(10) NOT NULL"), "binding has incompatible columns"),
    "created_at_text": (_binding(created_at="TEXT NOT NULL"), "binding has incompatible columns"),
    "created_at_timestamptz": (_binding(created_at="TIMESTAMP WITH TIME ZONE NOT NULL"),
                               "binding has incompatible columns"),
    "operation_id_bigint": (_binding(operation_id=f"BIGINT NOT NULL {OPERATION_FK}"),
                            "binding has incompatible columns"),
    # 15. nullability, server defaults, column set
    "user_id_nullable": (_binding(user_id=f"INTEGER {USER_FK}"), "binding has incompatible columns"),
    "state_nullable": (_binding(state="VARCHAR(16)"), "binding has incompatible columns"),
    "state_server_default": (_binding(state="VARCHAR(16) NOT NULL DEFAULT 'COMMITTED'"),
                             "binding has incompatible columns"),
    "created_at_server_default": (_binding(created_at="TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()"),
                                  "binding has incompatible columns"),
    "extra_column": (_binding(extra="surprise TEXT"), "binding has incompatible columns"),
    "missing_column": (_binding(after=(ALTER + "DROP COLUMN updated_at",)), "binding has incompatible columns"),
    # keys
    "primary_key_widened": (_binding(key="PRIMARY KEY (operation_id, user_id)"),
                            "binding has incompatible primary key"),
    "ref_unique_missing": (_binding(unique=""), "binding has incompatible reference uniqueness"),
    "ref_unique_renamed": (_binding(unique="CONSTRAINT uq_other UNIQUE (reservation_ref)"),
                           "binding has incompatible reference uniqueness"),
    # 9-10. owner index
    "owner_index_state_only": (_binding(index="(state)"), "binding has incompatible owner index"),
    "owner_index_user_only": (_binding(index="(user_id)"), "binding has incompatible owner index"),
    "owner_index_reversed": (_binding(index="(state, user_id)"), "binding has incompatible owner index"),
    "owner_index_unique": (_binding(unique_index=True), "binding has incompatible owner index"),
    "owner_index_partial": (_binding(index="(user_id, state) WHERE state = 'RESERVED'"),
                            "binding has incompatible owner index"),
    "owner_index_descending": (_binding(index="(user_id, state DESC)"), "binding has incompatible owner index"),
    # 11-12. foreign keys
    "operation_fk_wrong_column": (_binding(
        operation_id="INTEGER NOT NULL REFERENCES training_plan_generation_operation(alt_id) ON DELETE CASCADE",
        after=()), "binding has incompatible ownership cascades"),
    "user_fk_wrong_table": (_binding(user_id="INTEGER NOT NULL REFERENCES training_plan(id) ON DELETE CASCADE"),
                            "binding has incompatible ownership cascades"),
    "operation_fk_no_cascade": (_binding(
        operation_id="INTEGER NOT NULL REFERENCES training_plan_generation_operation(id)"),
        "binding has incompatible ownership cascades"),
    "user_fk_set_null": (_binding(user_id='INTEGER NOT NULL REFERENCES "user"(id) ON DELETE SET NULL'),
                         "binding has incompatible ownership cascades"),
    "extra_foreign_key": (_binding(after=(
        ALTER + "ADD FOREIGN KEY (reservation_ref) REFERENCES ai_usage_reservation(public_id)",)),
        "binding has incompatible ownership cascades"),
    "user_fk_not_valid": (_binding(user_id="INTEGER NOT NULL", after=(
        ALTER + f"ADD FOREIGN KEY (user_id) {USER_FK} NOT VALID",)), "binding has incompatible state check"),
    # operation metering mode
    "metered_not_null": (_sql(OPERATION + "ADD COLUMN allowance_metered BOOLEAN NOT NULL DEFAULT false"),
                         "incompatible training_plan_generation_operation.allowance_metered"),
    "metered_integer": (_sql(OPERATION + "ADD COLUMN allowance_metered INTEGER"),
                        "incompatible training_plan_generation_operation.allowance_metered"),
    "metered_server_default": (_sql(OPERATION + "ADD COLUMN allowance_metered BOOLEAN DEFAULT true"),
                               "incompatible training_plan_generation_operation.allowance_metered"),
    # 13-15. billing evidence and the tri-state Boolean (b1e4 is the authority, a9c1 unchanged)
    "evidence_char24": (_sql(RESERVATION + "ADD COLUMN billing_evidence CHAR(24)"),
                        "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_varchar10": (_sql(RESERVATION + "ADD COLUMN billing_evidence VARCHAR(10)"),
                           "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_varchar32": (_sql(RESERVATION + "ADD COLUMN billing_evidence VARCHAR(32)"),
                           "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_integer": (_sql(RESERVATION + "ADD COLUMN billing_evidence INTEGER"),
                         "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_text": (_sql(RESERVATION + "ADD COLUMN billing_evidence TEXT"),
                      "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_not_null": (_sql(RESERVATION + "ADD COLUMN billing_evidence VARCHAR(24) NOT NULL DEFAULT 'UNKNOWN'"),
                          "incompatible ai_usage_reservation.billing_evidence"),
    "evidence_server_default": (_sql(
        RESERVATION + "ADD COLUMN billing_evidence VARCHAR(24) DEFAULT 'CONFIRMED_BILLED'"),
        "incompatible ai_usage_reservation.billing_evidence"),
    "billed_integer": (_sql(RESERVATION + "ALTER COLUMN provider_billed TYPE INTEGER USING provider_billed::int"),
                       "incompatible ai_usage_reservation.provider_billed"),
    "billed_server_default": (_sql(RESERVATION + "ALTER COLUMN provider_billed SET DEFAULT false"),
                              "incompatible ai_usage_reservation.provider_billed"),
    # create_all-first ledger that a9c1 (unchanged, name-only for the later column) passes on
    "ledger_first_exact_delta": (("ledger", _ledger(RESERVATION + "ADD COLUMN billing_evidence VARCHAR(24)")),
                                 None),
    "ledger_first_evidence_char24": (("ledger", _ledger(RESERVATION + "ADD COLUMN billing_evidence CHAR(24)")),
                                     "incompatible ai_usage_reservation.billing_evidence"),
    "ledger_first_evidence_server_default": (("ledger", _ledger(
        RESERVATION + "ADD COLUMN billing_evidence VARCHAR(24) DEFAULT 'CONFIRMED_BILLED'")),
        "incompatible ai_usage_reservation.billing_evidence"),
}


def _schema(conn):
    """Every column, constraint (with validation) and index of the touched tables."""
    tables = ("ai_usage_reservation", "training_plan_generation_operation", BINDING)
    return (
        conn.exec_driver_sql(
            "SELECT table_name, column_name, data_type, character_maximum_length, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = 'public' AND table_name IN %(t)s "
            "ORDER BY 1, 2", {"t": tables}).all(),
        conn.exec_driver_sql(
            "SELECT conrelid::regclass::text, conname, convalidated, pg_get_constraintdef(oid) "
            "FROM pg_constraint WHERE conrelid::regclass::text IN %(t)s ORDER BY 1, 2", {"t": tables}).all(),
        conn.exec_driver_sql(
            "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE tablename IN %(t)s ORDER BY 1, 2",
            {"t": tables}).all(),
    )


@pytest.mark.parametrize("case", list(CASES))
def test_b1e4_accepts_only_its_exact_existing_schema(migrate_engine, case):
    make, refusal = CASES[case]
    ledger_first = isinstance(make, tuple)
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql("CREATE TABLE training_plan (id INTEGER PRIMARY KEY)")
        if ledger_first:
            make[1](conn)
        for revision in (LEDGER, OPERATIONS, MARKER):
            _run(conn, _module(revision).upgrade)
        if case == "operation_fk_wrong_column":
            conn.exec_driver_sql(OPERATION + "ADD COLUMN alt_id INTEGER UNIQUE")
        if not ledger_first:
            make(conn)
        before = _schema(conn)
    if refusal is None:
        with migrate_engine.begin() as conn:
            _run(conn, _module(HEAD).upgrade)
            _run(conn, _module(HEAD).upgrade)  # reentrant: re-verifies what it just accepted
            after = _schema(conn)
            validated = dict(conn.exec_driver_sql(
                "SELECT conname, convalidated FROM pg_constraint WHERE conrelid = %(t)s::regclass",
                {"t": BINDING}).all())
        assert validated["ck_training_plan_generation_allowance_state"] is True
        assert all(validated.values())
        assert [i for i in after[2] if i[1] == "ix_training_plan_generation_allowance_owner"][0][2].endswith(
            "USING btree (user_id, state)")
        return
    with pytest.raises(RuntimeError) as refused:
        with migrate_engine.begin() as conn:
            _run(conn, _module(HEAD).upgrade)
    assert refusal in str(refused.value), (case, str(refused.value))
    with migrate_engine.begin() as conn:
        assert _schema(conn) == before  # fail closed: nothing half-applied


def test_reentrant_upgrade_keeps_historical_and_unknown_rows_exactly(migrate_engine):
    """Seeded b0d3 database -> b1e4 -> b1e4 again: no row erased, reclassified or backfilled."""
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql("CREATE TABLE training_plan (id INTEGER PRIMARY KEY)")
        for revision in (LEDGER, OPERATIONS, MARKER):
            _run(conn, _module(revision).upgrade)
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1), (2)')
        conn.exec_driver_sql(
            "INSERT INTO ai_starter_grant (user_id, policy_version, granted_at) VALUES (1, 'v', now())")
        conn.exec_driver_sql(
            "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, granted, used,"
            " reserved, billed_releases, created_at, updated_at) VALUES "
            "(1, 'STARTER', 'training_generation', 'NEVER', 'v', 5, 2, 1, 1, now(), now()),"
            "(2, 'PREMIUM', 'coach', '2026-W42', 'v', 9, 0, 0, 0, now(), now())")
        conn.exec_driver_sql(
            "INSERT INTO ai_usage_reservation (public_id, user_id, bucket_id, feature, key_digest, state, outcome,"
            " provider_billed, created_at, expires_at, finalized_at) VALUES "
            "(repeat('a', 32), 1, 1, 'training_generation', 'd1', 'RESERVED', NULL, false, now(), now(), NULL),"
            "(repeat('b', 32), 1, 1, 'training_generation', 'd2', 'RELEASED', 'provider_error', true,"
            " now(), now(), now()),"
            "(repeat('c', 32), 1, 1, 'training_generation', 'd3', 'COMMITTED', 'success', false,"
            " now(), now(), now())")
        conn.exec_driver_sql(
            "INSERT INTO training_plan_generation_operation (user_id, idempotency_key, request_fingerprint, status,"
            " attempt_count, quota_reserved, quota_week, created_at, updated_at, provider_dispatch_possible) VALUES "
            "(1, 'h-null', 'f', 'SUCCEEDED', 1, false, NULL, now(), now(), NULL),"
            "(1, 'h-true', 'f', 'FAILED', 1, false, NULL, now(), now(), true),"
            "(2, 'h-false', 'f', 'IN_PROGRESS', 1, true, '2026-W42', now(), now(), false)")

    def rows(conn):
        return (
            conn.exec_driver_sql("SELECT * FROM \"user\" ORDER BY id").all(),
            conn.exec_driver_sql("SELECT * FROM ai_starter_grant ORDER BY user_id").all(),
            conn.exec_driver_sql("SELECT * FROM ai_allowance_bucket ORDER BY id").all(),
            conn.exec_driver_sql(
                "SELECT id, public_id, user_id, bucket_id, feature, key_digest, state, outcome, provider_billed,"
                " created_at, expires_at, finalized_at FROM ai_usage_reservation ORDER BY id").all(),
            conn.exec_driver_sql(
                "SELECT id, user_id, idempotency_key, request_fingerprint, status, attempt_count, quota_reserved,"
                " quota_week, created_at, updated_at, provider_dispatch_possible"
                " FROM training_plan_generation_operation ORDER BY id").all())

    with migrate_engine.begin() as conn:
        before = rows(conn)
        _run(conn, _module(HEAD).upgrade)
        assert rows(conn) == before
        assert conn.exec_driver_sql(
            "SELECT (SELECT count(*) FROM ai_usage_reservation WHERE billing_evidence IS NOT NULL),"
            " (SELECT count(*) FROM ai_usage_reservation WHERE provider_billed IS NULL),"
            " (SELECT count(*) FROM training_plan_generation_operation WHERE allowance_metered IS NOT NULL),"
            f" (SELECT count(*) FROM {BINDING})").one() == (0, 0, 0, 0)
        assert [billed for (billed,) in conn.exec_driver_sql(
            "SELECT provider_billed FROM ai_usage_reservation ORDER BY id")] == [False, True, False]
        # An UNKNOWN hold written by new code survives a reentrant upgrade as NULL, not False.
        conn.exec_driver_sql(
            "INSERT INTO ai_usage_reservation (public_id, user_id, bucket_id, feature, key_digest, state,"
            " provider_billed, billing_evidence, created_at, expires_at) VALUES"
            " (repeat('d', 32), 1, 1, 'training_generation', 'd4', 'RESERVED', NULL, 'UNKNOWN', now(), now())")
        with_unknown = rows(conn)
    with migrate_engine.begin() as conn:
        _run(conn, _module(HEAD).upgrade)
        assert rows(conn) == with_unknown
        assert conn.exec_driver_sql(
            "SELECT provider_billed, billing_evidence FROM ai_usage_reservation WHERE key_digest = 'd4'"
        ).one() == (None, "UNKNOWN")
