"""Completion-authority fence and capability floor (PRIV-K6 rollback gate, Layer A).

Expand only, and DORMANT until a separately raised floor. The revision adds two
tables and a set of PostgreSQL trigger functions:

* ``completion_capability_floor`` -- at most one row per capability, insert-only.
  The row ``workout_completion_claims_v1`` is the durable, monotonic floor. It
  is raised later, under the exclusive floor lock, by ``flask completion-floor
  raise``; this revision never inserts it.
* ``completion_claim_transaction`` -- one row per ``workout_completion_day``
  claim, written ONLY by the claim's own AFTER INSERT trigger, binding the
  claim to the 64-bit top-level transaction id (``pg_current_xact_id()``) that
  inserted it. A caller cannot choose that value.

Enforced at COMMIT by deferred constraint triggers, for every writer whatever
its image, script or principal (only DDL bypasses them):

* a claim commits only once the floor is committed and visible (AXF01), only
  with its own binding (AXF05), and a photo-free claim never beside committed
  historical ``PumpCheck.date_key`` evidence for the same owner/day (AXF04);
* once the floor is raised (or any claim exists), a dated ``PumpCheck`` or a
  completion-marker ``WorkoutLog`` commits only if THIS transaction inserted the
  owner's claim -- for the same day, for a PumpCheck (AXF02);
* every check above first takes the floor lock SHARED; the raise takes it
  EXCLUSIVE, so the raise waits for every commit already past a check and every
  later commit sees the floor;
* the floor is insert-only and can only be inserted under the exclusive lock
  (AXF03); claims are immutable and retained while their owner exists, and
  bindings are written and removed only by triggers (AXF05);
* the checks require READ COMMITTED: under REPEATABLE READ or SERIALIZABLE a
  commit-time read sees the transaction snapshot, not the committed floor
  (AXF06).

Every relation is schema-qualified and every function pins ``search_path`` to
``pg_catalog, pg_temp``: an unqualified name would resolve a session's TEMP
table first and could be satisfied by a forged claim. Every trigger is ENABLE
ALWAYS, so ``session_replication_role = replica`` does not switch it off.

PostgreSQL only (13+, ``pg_current_xact_id``; qualified on 16). SQLite gets the
two tables and no enforcement, like the advisory locks elsewhere.

Boot runs ``create_all`` before upgrading on a fresh database, so the two
tables may already exist; they are accepted only with exactly this DDL. A
re-entered upgrade is accepted only when every fence object matches exactly;
a partial or altered fence fails closed and is never silently "repaired".
Downgrade refuses while a floor, claim or binding row exists.

This file's content is pinned in ``scripts/migration_expand_contract.py``
(``REVIEWED_DDL``): trigger DDL cannot be proven overlap-safe by the static
gate, so this exact text was reviewed instead and any edit fails the gate.
"""
from alembic import op
import sqlalchemy as sa

revision = "d7e3a9c1f5b8"
down_revision = "f9c6d0a1b2e3"
branch_labels = None
depends_on = None
expand_contract = "expand"

SCHEMA = "public"
FLOOR = "completion_capability_floor"
BINDING = "completion_claim_transaction"
CLAIM = "workout_completion_day"
CAPABILITY = "workout_completion_claims_v1"
MARKER = "Antrenman tamamlandı (Pump Check)"
# sha256(b"fitx:completion-capability-floor:v1\0")[:8], signed big-endian; the
# exclusive holder appears in pg_locks as classid/objid = high/low 32 bits.
FLOOR_LOCK_KEY = -3391128469959823719
FLOOR_LOCK_CLASSID = 3505408671
FLOOR_LOCK_OBJID = 2689904281
MIN_SERVER_VERSION = 130000

FLOOR_CAPABILITY_CHECK = "ck_completion_capability_floor_capability"
FLOOR_REVISION_CHECK = "ck_completion_capability_floor_revision"
BINDING_TXID_INDEX = "ix_completion_claim_transaction_claim_txid"

_ISOLATION = """
    IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'completion fence requires READ COMMITTED'
            USING ERRCODE = 'AXF06';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock_shared(-3391128469959823719);"""

# Function bodies are compared byte-for-byte with pg_proc.prosrc on re-entry.
FUNCTIONS = {
    "completion_fence_bind_claim": """
BEGIN
    INSERT INTO public.completion_claim_transaction (claim_id, claim_txid)
    VALUES (NEW.id, pg_catalog.pg_current_xact_id()::text::bigint);
    RETURN NULL;
END
""",
    "completion_fence_claim_commit": """
BEGIN""" + _ISOLATION + """
    IF NOT EXISTS (SELECT 1 FROM public.completion_capability_floor f
                   WHERE f.capability = 'workout_completion_claims_v1') THEN
        RAISE EXCEPTION 'completion claims are not authorized before the capability floor is raised'
            USING ERRCODE = 'AXF01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.completion_claim_transaction b
                   WHERE b.claim_id = NEW.id
                     AND b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint) THEN
        RAISE EXCEPTION 'completion claim is not bound to this transaction'
            USING ERRCODE = 'AXF05';
    END IF;
    IF NEW.proof = 'none' AND EXISTS (
            SELECT 1 FROM public.pump_check p
            WHERE p.user_id = NEW.user_id AND p.date_key = NEW.date_key) THEN
        RAISE EXCEPTION 'photo-free completion claim conflicts with historical completion evidence'
            USING ERRCODE = 'AXF04';
    END IF;
    RETURN NULL;
END
""",
    "completion_fence_dated_pump_check": """
BEGIN""" + _ISOLATION + """
    IF NOT EXISTS (SELECT 1 FROM public.completion_capability_floor f
                   WHERE f.capability = 'workout_completion_claims_v1')
       AND NOT EXISTS (SELECT 1 FROM public.workout_completion_day) THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
            SELECT 1 FROM public.completion_claim_transaction b
            JOIN public.workout_completion_day c ON c.id = b.claim_id
            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint
              AND c.user_id = NEW.user_id AND c.date_key = NEW.date_key) THEN
        RAISE EXCEPTION 'completion evidence without a claim in this transaction (writer below capability floor)'
            USING ERRCODE = 'AXF02';
    END IF;
    RETURN NULL;
END
""",
    "completion_fence_marker": """
BEGIN""" + _ISOLATION + """
    IF NOT EXISTS (SELECT 1 FROM public.completion_capability_floor f
                   WHERE f.capability = 'workout_completion_claims_v1')
       AND NOT EXISTS (SELECT 1 FROM public.workout_completion_day) THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
            SELECT 1 FROM public.completion_claim_transaction b
            JOIN public.workout_completion_day c ON c.id = b.claim_id
            WHERE b.claim_txid = pg_catalog.pg_current_xact_id()::text::bigint
              AND c.user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'completion evidence without a claim in this transaction (writer below capability floor)'
            USING ERRCODE = 'AXF02';
    END IF;
    RETURN NULL;
END
""",
    "completion_fence_floor_admission": """
BEGIN
    IF NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_locks l
            WHERE l.locktype = 'advisory' AND l.granted
              AND l.mode = 'ExclusiveLock'
              AND l.pid = pg_catalog.pg_backend_pid()
              AND l.database = (SELECT d.oid FROM pg_catalog.pg_database d
                                WHERE d.datname = pg_catalog.current_database())
              AND l.classid = 3505408671::oid AND l.objid = 2689904281::oid
              AND l.objsubid = 1) THEN
        RAISE EXCEPTION 'the capability floor is raised only under the exclusive floor lock'
            USING ERRCODE = 'AXF03';
    END IF;
    RETURN NEW;
END
""",
    "completion_fence_floor_guard": """
BEGIN
    RAISE EXCEPTION 'the completion capability floor is monotonic'
        USING ERRCODE = 'AXF03';
END
""",
    "completion_fence_claim_immutable": """
BEGIN
    RAISE EXCEPTION 'completion claims are immutable' USING ERRCODE = 'AXF05';
END
""",
    "completion_fence_claim_retention": """
BEGIN
    IF EXISTS (SELECT 1 FROM public."user" u WHERE u.id = OLD.user_id) THEN
        RAISE EXCEPTION 'completion claims are retained while their owner exists'
            USING ERRCODE = 'AXF05';
    END IF;
    RETURN NULL;
END
""",
    "completion_fence_binding_guard": """
BEGIN
    IF TG_OP = 'UPDATE' OR pg_catalog.pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'claim bindings are written and removed only by the fence'
            USING ERRCODE = 'AXF05';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF NEW.claim_txid <> pg_catalog.pg_current_xact_id()::text::bigint THEN
        RAISE EXCEPTION 'claim binding names another transaction'
            USING ERRCODE = 'AXF05';
    END IF;
    RETURN NEW;
END
""",
    "completion_fence_retained_truncate": """
BEGIN
    IF EXISTS (SELECT 1 FROM public.workout_completion_day)
       OR EXISTS (SELECT 1 FROM public.completion_claim_transaction) THEN
        RAISE EXCEPTION 'completion claims cannot be truncated'
            USING ERRCODE = 'AXF05';
    END IF;
    RETURN NULL;
END
""",
}

# (name, table, CREATE statement) in install order. tgname order decides the
# firing order of same-event triggers; the binding row is written before any
# deferred check can run.
TRIGGERS = (
    ("completion_fence_bind", CLAIM,
     "CREATE TRIGGER completion_fence_bind AFTER INSERT ON public.workout_completion_day "
     "FOR EACH ROW EXECUTE FUNCTION public.completion_fence_bind_claim()"),
    ("completion_fence_claim_commit", CLAIM,
     "CREATE CONSTRAINT TRIGGER completion_fence_claim_commit AFTER INSERT ON "
     "public.workout_completion_day DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_claim_commit()"),
    ("completion_fence_claim_immutable", CLAIM,
     "CREATE TRIGGER completion_fence_claim_immutable BEFORE UPDATE ON "
     "public.workout_completion_day FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_claim_immutable()"),
    ("completion_fence_claim_retention", CLAIM,
     "CREATE CONSTRAINT TRIGGER completion_fence_claim_retention AFTER DELETE ON "
     "public.workout_completion_day DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_claim_retention()"),
    ("completion_fence_claim_truncate", CLAIM,
     "CREATE TRIGGER completion_fence_claim_truncate BEFORE TRUNCATE ON "
     "public.workout_completion_day FOR EACH STATEMENT "
     "EXECUTE FUNCTION public.completion_fence_retained_truncate()"),
    ("completion_fence_binding_guard", BINDING,
     "CREATE TRIGGER completion_fence_binding_guard BEFORE INSERT OR UPDATE OR DELETE ON "
     "public.completion_claim_transaction FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_binding_guard()"),
    ("completion_fence_binding_truncate", BINDING,
     "CREATE TRIGGER completion_fence_binding_truncate BEFORE TRUNCATE ON "
     "public.completion_claim_transaction FOR EACH STATEMENT "
     "EXECUTE FUNCTION public.completion_fence_retained_truncate()"),
    ("completion_fence_floor_admission", FLOOR,
     "CREATE TRIGGER completion_fence_floor_admission BEFORE INSERT ON "
     "public.completion_capability_floor FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_floor_admission()"),
    ("completion_fence_floor_guard", FLOOR,
     "CREATE TRIGGER completion_fence_floor_guard BEFORE UPDATE OR DELETE ON "
     "public.completion_capability_floor FOR EACH ROW "
     "EXECUTE FUNCTION public.completion_fence_floor_guard()"),
    ("completion_fence_floor_truncate", FLOOR,
     "CREATE TRIGGER completion_fence_floor_truncate BEFORE TRUNCATE ON "
     "public.completion_capability_floor FOR EACH STATEMENT "
     "EXECUTE FUNCTION public.completion_fence_floor_guard()"),
    ("completion_fence_dated_pump_check", "pump_check",
     "CREATE CONSTRAINT TRIGGER completion_fence_dated_pump_check AFTER INSERT OR "
     "UPDATE OF date_key, user_id ON public.pump_check DEFERRABLE INITIALLY DEFERRED "
     "FOR EACH ROW WHEN (NEW.date_key IS NOT NULL) "
     "EXECUTE FUNCTION public.completion_fence_dated_pump_check()"),
    ("completion_fence_marker", "workout_log",
     "CREATE CONSTRAINT TRIGGER completion_fence_marker AFTER INSERT OR UPDATE OF "
     "exercise_name, user_id ON public.workout_log DEFERRABLE INITIALLY DEFERRED "
     "FOR EACH ROW WHEN (NEW.exercise_name = 'Antrenman tamamlandı (Pump Check)') "
     "EXECUTE FUNCTION public.completion_fence_marker()"),
)

# pg_get_triggerdef() of each trigger exactly as PostgreSQL 16 renders it.
# Unfamiliar text (another major version, an edited trigger) fails closed.
TRIGGER_DEFS = {
    "completion_fence_bind":
        "CREATE TRIGGER completion_fence_bind AFTER INSERT ON public.workout_completion_day "
        "FOR EACH ROW EXECUTE FUNCTION completion_fence_bind_claim()",
    "completion_fence_claim_commit":
        "CREATE CONSTRAINT TRIGGER completion_fence_claim_commit AFTER INSERT ON "
        "public.workout_completion_day DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_claim_commit()",
    "completion_fence_claim_immutable":
        "CREATE TRIGGER completion_fence_claim_immutable BEFORE UPDATE ON "
        "public.workout_completion_day FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_claim_immutable()",
    "completion_fence_claim_retention":
        "CREATE CONSTRAINT TRIGGER completion_fence_claim_retention AFTER DELETE ON "
        "public.workout_completion_day DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_claim_retention()",
    "completion_fence_claim_truncate":
        "CREATE TRIGGER completion_fence_claim_truncate BEFORE TRUNCATE ON "
        "public.workout_completion_day FOR EACH STATEMENT "
        "EXECUTE FUNCTION completion_fence_retained_truncate()",
    "completion_fence_binding_guard":
        "CREATE TRIGGER completion_fence_binding_guard BEFORE INSERT OR DELETE OR UPDATE ON "
        "public.completion_claim_transaction FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_binding_guard()",
    "completion_fence_binding_truncate":
        "CREATE TRIGGER completion_fence_binding_truncate BEFORE TRUNCATE ON "
        "public.completion_claim_transaction FOR EACH STATEMENT "
        "EXECUTE FUNCTION completion_fence_retained_truncate()",
    "completion_fence_floor_admission":
        "CREATE TRIGGER completion_fence_floor_admission BEFORE INSERT ON "
        "public.completion_capability_floor FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_floor_admission()",
    "completion_fence_floor_guard":
        "CREATE TRIGGER completion_fence_floor_guard BEFORE DELETE OR UPDATE ON "
        "public.completion_capability_floor FOR EACH ROW "
        "EXECUTE FUNCTION completion_fence_floor_guard()",
    "completion_fence_floor_truncate":
        "CREATE TRIGGER completion_fence_floor_truncate BEFORE TRUNCATE ON "
        "public.completion_capability_floor FOR EACH STATEMENT "
        "EXECUTE FUNCTION completion_fence_floor_guard()",
    "completion_fence_dated_pump_check":
        "CREATE CONSTRAINT TRIGGER completion_fence_dated_pump_check AFTER INSERT OR "
        "UPDATE OF date_key, user_id ON public.pump_check DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW WHEN ((new.date_key IS NOT NULL)) "
        "EXECUTE FUNCTION completion_fence_dated_pump_check()",
    "completion_fence_marker":
        "CREATE CONSTRAINT TRIGGER completion_fence_marker AFTER INSERT OR UPDATE OF "
        "exercise_name, user_id ON public.workout_log DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW WHEN (((new.exercise_name)::text = "
        "'Antrenman tamamlandı (Pump Check)'::text)) "
        "EXECUTE FUNCTION completion_fence_marker()",
}

FUNCTION_CONFIG = ["search_path=pg_catalog, pg_temp"]


def _floor_columns():
    return [
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raised_by_revision", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("capability"),
        sa.CheckConstraint(f"capability IN ('{CAPABILITY}')", name=FLOOR_CAPABILITY_CHECK),
    ]


def _binding_columns():
    return [
        sa.Column("claim_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("claim_txid", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], [f"{CLAIM}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("claim_id"),
    ]


# Exact pg_get_constraintdef() forms of the floor CHECKs (PostgreSQL 16).
_PG_CHECKS = {
    FLOOR_CAPABILITY_CHECK:
        "CHECK (((capability)::text = 'workout_completion_claims_v1'::text))",
    FLOOR_REVISION_CHECK:
        "CHECK (((raised_by_revision)::text ~ '^[0-9a-f]{40}$'::text))",
}


def _fail(what):
    raise RuntimeError(f"completion authority fence has incompatible {what}")


def _create_tables(bind):
    inspector = sa.inspect(bind)
    if inspector.has_table(FLOOR):
        _verify_floor_table(bind)
    else:
        op.create_table(FLOOR, *_floor_columns())
        if bind.dialect.name == "postgresql":
            op.execute(
                "ALTER TABLE public.completion_capability_floor ADD CONSTRAINT "
                "ck_completion_capability_floor_revision "
                "CHECK (raised_by_revision ~ '^[0-9a-f]{40}$')")
    if inspector.has_table(BINDING):
        _verify_binding_table(bind)
    else:
        op.create_table(BINDING, *_binding_columns())
        op.create_index(BINDING_TXID_INDEX, BINDING, ["claim_txid"], unique=False)


def _same_type(bind, column, type_, nullable):
    return (column["type"].compile(dialect=bind.dialect) == type_.compile(dialect=bind.dialect)
            and column["nullable"] is nullable)


def _verify_columns(bind, table, expected):
    inspector = sa.inspect(bind)
    columns = {c["name"]: c for c in inspector.get_columns(table)}
    expected = [c for c in expected if isinstance(c, sa.Column)]
    if set(columns) != {c.name for c in expected} or not all(
            _same_type(bind, columns[c.name], c.type, c.nullable) for c in expected):
        _fail(f"{table} columns")
    if any(columns[c.name].get("default") is not None for c in expected):
        _fail(f"{table} column defaults")
    return inspector


def _pg_constraints(bind, table):
    rows = bind.execute(sa.text(
        "SELECT conname, contype, convalidated, pg_catalog.pg_get_constraintdef(oid) "
        "FROM pg_catalog.pg_constraint WHERE conrelid = pg_catalog.to_regclass(:t)"),
        {"t": f"{SCHEMA}.{table}"}).all()
    return {name: (kind, valid, text) for name, kind, valid, text in rows}


def _pg_table_constraints(bind, table, columns):
    constraints = _pg_constraints(bind, table)
    # PG18 catalogs NOT NULL constraints; PG16 reports nullability only through
    # the columns already checked by _verify_columns(). Normalize ONLY the
    # complete, validated set for those required columns, independent of names.
    not_null = [(valid, text) for kind, valid, text in constraints.values() if kind == "n"]
    expected = [(True, f"NOT NULL {c.name}") for c in columns
                if isinstance(c, sa.Column) and not c.nullable]
    if not_null and sorted(not_null) != sorted(expected):
        _fail(f"{table} NOT NULL constraints")
    return {name: state for name, state in constraints.items() if state[0] != "n"}


def _verify_floor_table(bind):
    inspector = _verify_columns(bind, FLOOR, _floor_columns())
    if inspector.get_pk_constraint(FLOOR)["constrained_columns"] != ["capability"]:
        _fail("floor primary key")
    if inspector.get_unique_constraints(FLOOR) or inspector.get_foreign_keys(FLOOR):
        _fail("floor constraints")
    if [i for i in inspector.get_indexes(FLOOR) if not i.get("duplicates_constraint")]:
        _fail("floor indexes")
    if bind.dialect.name != "postgresql":
        return
    constraints = _pg_table_constraints(bind, FLOOR, _floor_columns())
    checks = {name: text for name, (kind, valid, text) in constraints.items() if kind == "c"}
    if checks != _PG_CHECKS or not all(valid for kind, valid, _ in constraints.values()):
        _fail("floor checks")
    if {kind for kind, _, _ in constraints.values()} != {"p", "c"}:
        _fail("floor constraints")


def _verify_binding_table(bind):
    inspector = _verify_columns(bind, BINDING, _binding_columns())
    if inspector.get_pk_constraint(BINDING)["constrained_columns"] != ["claim_id"]:
        _fail("binding primary key")
    if inspector.get_unique_constraints(BINDING) or inspector.get_check_constraints(BINDING):
        _fail("binding constraints")
    foreign = {(tuple(f["constrained_columns"]), f["referred_table"],
                tuple(f["referred_columns"]), f.get("options", {}).get("ondelete"))
               for f in inspector.get_foreign_keys(BINDING)}
    if foreign != {(("claim_id",), CLAIM, ("id",), "CASCADE")}:
        _fail("binding ownership cascade")
    indexes = [i for i in inspector.get_indexes(BINDING) if not i.get("duplicates_constraint")]
    if [(i["name"], tuple(i["column_names"]), bool(i.get("unique"))) for i in indexes] != [
            (BINDING_TXID_INDEX, ("claim_txid",), False)]:
        _fail("binding index")
    if bind.dialect.name == "postgresql":
        constraints = _pg_table_constraints(bind, BINDING, _binding_columns())
        if (sorted(kind for kind, _, _ in constraints.values()) != ["f", "p"]
                or not all(valid for _, valid, _ in constraints.values())):
            _fail("binding constraints")


def _fence_catalog(bind):
    """(functions, triggers) of the fence currently in the catalog."""
    functions = {name: (src, config, secdef, volatile, lang, nargs) for
                 name, src, config, secdef, volatile, lang, nargs in bind.execute(sa.text(
                     "SELECT p.proname, p.prosrc, p.proconfig, p.prosecdef, p.provolatile, "
                     "l.lanname, p.pronargs FROM pg_catalog.pg_proc p "
                     "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                     "JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
                     "WHERE n.nspname = :schema AND p.proname LIKE 'completion\\_fence\\_%'"),
                     {"schema": SCHEMA}).all()}
    triggers = {name: (table, enabled, definition) for name, table, enabled, definition in
                bind.execute(sa.text(
                    "SELECT t.tgname, c.relname, t.tgenabled, "
                    "pg_catalog.pg_get_triggerdef(t.oid) FROM pg_catalog.pg_trigger t "
                    "JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND NOT t.tgisinternal "
                    "AND (t.tgname LIKE 'completion\\_fence\\_%' "
                    "     OR c.relname IN (:floor, :binding))"),
                    {"schema": SCHEMA, "floor": FLOOR, "binding": BINDING}).all()}
    return functions, triggers


def _verify_relations(bind):
    """Every fenced table is an ordinary, permanent table. An UNLOGGED table is
    emptied by crash recovery, so an UNLOGGED floor would silently fall; a view
    or partitioned parent does not fire these row triggers the same way. The
    tables the fence READS (floor, binding, claim) carry no row-level security:
    a policy hiding the floor row from a writer would make the fence dormant."""
    rows = bind.execute(sa.text(
        "SELECT c.relname, c.relkind, c.relpersistence, c.relrowsecurity, "
        "c.relforcerowsecurity FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :schema AND c.relname IN "
        "(:floor, :binding, :claim, 'pump_check', 'workout_log')"),
        {"schema": SCHEMA, "floor": FLOOR, "binding": BINDING, "claim": CLAIM}).all()
    found = {name: (kind, persistence, rls, forced)
             for name, kind, persistence, rls, forced in rows}
    if set(found) != {FLOOR, BINDING, CLAIM, "pump_check", "workout_log"}:
        _fail("fenced relations")
    for name, (kind, persistence, rls, forced) in found.items():
        if kind != "r" or persistence != "p":
            _fail(f"{name} relation kind or persistence")
        if name in (FLOOR, BINDING, CLAIM) and (rls or forced):
            _fail(f"{name} row-level security")


def verify_fence(bind):
    """Raise unless the installed fence is exactly this revision's.

    Read-only. Also used by ``flask completion-floor`` (loaded from this file),
    so release tooling verifies the very text that was reviewed."""
    if bind.execute(sa.text("SELECT pg_catalog.current_setting('server_version_num')::int"
                            )).scalar() < MIN_SERVER_VERSION:
        _fail("PostgreSQL version")
    for table in (FLOOR, BINDING, CLAIM, "pump_check", "workout_log", "user"):
        if bind.execute(sa.text("SELECT pg_catalog.to_regclass(:t)"),
                        {"t": f'{SCHEMA}."{table}"'}).scalar() is None:
            _fail(f"schema ({table} is not in {SCHEMA})")
    _verify_relations(bind)
    _verify_floor_table(bind)
    _verify_binding_table(bind)
    functions, triggers = _fence_catalog(bind)
    if set(functions) != set(FUNCTIONS):
        _fail("function set")
    for name, body in FUNCTIONS.items():
        if functions[name] != (body, FUNCTION_CONFIG, False, "v", "plpgsql", 0):
            _fail(f"function {name}")
    if set(triggers) != {name for name, _, _ in TRIGGERS}:
        _fail("trigger set")
    for name, table, _ in TRIGGERS:
        expected = TRIGGER_DEFS[name]
        # The function is rendered schema-qualified only when the reading
        # session's search_path does not make it visible.
        function = expected.rsplit("EXECUTE FUNCTION ", 1)[1]
        forms = {expected, expected.replace(f"EXECUTE FUNCTION {function}",
                                            f"EXECUTE FUNCTION {SCHEMA}.{function}")}
        table_, enabled, definition = triggers[name]
        if table_ != table or enabled != "A" or definition not in forms:
            _fail(f"trigger {name}")
    rules = bind.execute(sa.text(
        "SELECT count(*) FROM pg_catalog.pg_rewrite r "
        "JOIN pg_catalog.pg_class c ON c.oid = r.ev_class "
        "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :schema AND c.relname IN "
        "(:floor, :binding, :claim, 'pump_check', 'workout_log')"),
        {"schema": SCHEMA, "floor": FLOOR, "binding": BINDING, "claim": CLAIM}).scalar()
    if rules:
        _fail("rewrite rules")


def _install_fence(bind):
    if bind.execute(sa.text("SELECT pg_catalog.current_setting('server_version_num')::int"
                            )).scalar() < MIN_SERVER_VERSION:
        _fail("PostgreSQL version")
    if bind.execute(sa.text("SELECT pg_catalog.current_schema()")).scalar() != SCHEMA:
        _fail("schema")
    for name, body in FUNCTIONS.items():
        op.execute(
            f"CREATE FUNCTION public.{name}() RETURNS trigger LANGUAGE plpgsql "
            "SECURITY INVOKER VOLATILE SET search_path = pg_catalog, pg_temp "
            f"AS $fence${body}$fence$")
    for name, table, statement in TRIGGERS:
        op.execute(statement)
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")


def upgrade():
    bind = op.get_bind()
    _create_tables(bind)
    if bind.dialect.name != "postgresql":
        return
    functions, triggers = _fence_catalog(bind)
    if not functions and not triggers:
        _install_fence(bind)
    verify_fence(bind)


def downgrade():
    bind = op.get_bind()
    for table in (FLOOR, CLAIM, BINDING):
        if bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar():
            # A raised floor or a retained claim is the durable record that
            # historical-only writers are fenced; removing it would let them
            # complete K6 days again.
            raise RuntimeError("the completion authority fence must be retained")
    if bind.dialect.name == "postgresql":
        for name, table, _ in reversed(TRIGGERS):
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{table}")
        for name in FUNCTIONS:
            op.execute(f"DROP FUNCTION IF EXISTS public.{name}()")
    op.drop_index(BINDING_TXID_INDEX, table_name=BINDING)
    op.drop_table(BINDING)
    op.drop_table(FLOOR)

