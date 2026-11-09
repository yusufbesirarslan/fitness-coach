"""R6 release qualification: the exact four-migration chain from the live head.

Production's last verified Alembic head is ``c6f8b0d2e4a1`` (restore receipt,
2026-11-09). The release candidate adds exactly four forward-only migrations:

    c6f8b0d2e4a1 -> d8f1a3c5e7b9 -> f01c0a1e0001 -> e1a5f0c3d2b4 -> f01e2a5e0002

Each migration has its own isolated test on synthetic tables. This module is
the CHAIN qualification on a database built only by the real Alembic history:

* the graph is exactly those four revisions, linear, one head;
* a database at ``c6f8b0d2e4a1`` populated the way production is (completion
  floor raised, historical Pump Check evidence, photo-free claims, legacy
  FatSecret rows, narrow-scope allowance buckets) is brought to head by the
  real ``flask release-prepare``; every pre-existing row is unchanged, nothing
  is backfilled, deleted or retroactively authorized (no consent rows, no
  erasure rows), and the O2-B completion-claim retention fence is installed;
* the whole chain runs in ONE transaction (migrations/env.py): a lock that
  blocks the third migration rolls the first two back too, the previous
  (``c6f8b0d2e4a1``) writer still boots, and a plain rerun completes;
* the R6 forward-recovery gate, given the true four-revision delta, admits
  exactly the post-migration states; after migration the previous writer
  cannot boot and the candidate can;
* downgrade refuses once erasure/legal-hold evidence exists.

This is disposable-schema qualification. It is NOT a rehearsal against a
restored copy of production data.

Gating: ``pg_concurrency`` marker and a disposable ``PG_TEST_DATABASE_URL``.
In the CI job ``R6_FORWARD_CHAIN_REQUIRED=1`` turns an unreachable database
into a failure instead of a skip. Never point it at a database you care about.
"""
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest
import sqlalchemy as sa

if (os.environ.get("R6_FORWARD_CHAIN_REQUIRED") == "1"
        and os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1"):
    raise RuntimeError("R6 chain qualification requires FITX_PG_CONCURRENCY_TEST=1")

from tests.test_r6_dual_revision_pg import (  # noqa: E402,F401 - pg_url is a fixture
    _boot_tool, _dispose, _versions, pg_url,
)

pytestmark = pytest.mark.pg_concurrency

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import migration_expand_contract as mec  # noqa: E402
import r6_deploy_transaction as tx  # noqa: E402

LIVE_HEAD = "c6f8b0d2e4a1"
CHAIN = ("d8f1a3c5e7b9", "f01c0a1e0001", "e1a5f0c3d2b4", "f01e2a5e0002")
HEAD = CHAIN[-1]
FLOOR_REVISION = "e" * 40
PREV_SHA = "a" * 40
CAND_SHA = "c" * 40

# Application tables the seed populates; every one exists at LIVE_HEAD.
SEEDED = ("user", "meal_log", "custom_meal", "custom_meal_item", "weekly_check_in",
          "pump_check", "workout_log", "workout_completion_day", "ai_allowance_bucket",
          "completion_capability_floor")
NEW_TABLES = ("health_consent_state", "health_consent_event", "meal_log_retained_total",
              "health_erasure", "health_legal_hold")
PROVIDER_COLUMNS = {"meal_log": ("provider_items", "provider_acquired_at", "provider_expired_at"),
                    "custom_meal_item": ("provider_acquired_at", "provider_expired_at")}


@pytest.fixture
def chain_url(request):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if os.environ.get("R6_FORWARD_CHAIN_REQUIRED") == "1":
        probe = sa.create_engine(url) if url else None
        try:
            assert probe is not None, "PG_TEST_DATABASE_URL is required"
            with probe.connect() as connection:
                connection.execute(sa.text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - reported as a failure
            pytest.fail(f"R6 chain qualification requires PostgreSQL: {exc!r}")
        finally:
            if probe is not None:
                probe.dispose()
    return request.getfixturevalue("pg_url")


def _engine(url):
    return sa.create_engine(url, poolclass=sa.pool.NullPool)


def _sql(url, statement, **params):
    engine = _engine(url)
    try:
        with engine.begin() as connection:
            result = connection.execute(sa.text(statement), params)
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _pgcode(callable_):
    try:
        callable_()
    except sa.exc.DBAPIError as exc:
        return getattr(exc.orig, "pgcode", None)
    return None


def _upgrade_to(monkeypatch, revision):
    import flask_migrate
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            flask_migrate.upgrade(revision=revision)
    finally:
        _dispose(tool)


def _release_prepare(monkeypatch):
    """The real one-off release step R6 runs (FITX_SKIP_DB_INIT=1 tool boot)."""
    tool = _boot_tool(monkeypatch)
    try:
        return tool.test_cli_runner().invoke(args=["release-prepare"])
    finally:
        _dispose(tool)


# ── seed: production-shaped state at the live head ──────────────────────────

def _seed(url):
    """Rows as production holds them at c6f8b0d2e4a1. Returns the owner ids."""
    from app.services import completion_fence

    engine = _engine(url)
    try:
        with engine.begin() as c:
            ids = {}
            # Production accounts carry a referral code (registration sets it;
            # release-prepare back-fills only missing ones, which is not part
            # of the migration chain and would otherwise show up as a diff).
            for index, name in enumerate(("r6_rich", "r6_claim_only", "r6_bystander")):
                ids[name] = c.execute(sa.text(
                    'INSERT INTO "user" (username, email, cognito_sub, referral_code) '
                    "VALUES (:n, :e, :s, :r) RETURNING id"),
                    {"n": name, "e": f"{name}@example.com", "s": f"sub-{name}",
                     "r": f"R6CHAIN{index:04d}"}).scalar()
            rich = ids["r6_rich"]
            # Historical (pre-floor) completion evidence and an ordinary log.
            c.execute(sa.text(
                "INSERT INTO pump_check (user_id, image_key, location_type, description, "
                "visibility, shared_friend_ids, likes_count, comments_count, reposts_count, "
                "valid, fallback, date_key, created_at) VALUES (:u, NULL, 'gym', 'synthetic', "
                "'private', '[]', 0, 0, 0, true, false, '2026-10-01', now())"), {"u": rich})
            c.execute(sa.text(
                "INSERT INTO workout_log (user_id, exercise_name, sets, reps, weight_kg, volume, "
                "created_at) VALUES (:u, 'Squat', 3, 5, 100, 1500, now())"), {"u": rich})
            # Legacy FatSecret-origin diary rows (no provenance columns yet).
            for source, kcal in (("search", 300), ("barcode", 120), ("manual", 450)):
                c.execute(sa.text(
                    "INSERT INTO meal_log (user_id, ogun, yemekler, kalori, tarih, source) "
                    "VALUES (:u, 'Öğle', :y, :k, '2026-11-01', :s)"),
                    {"u": rich, "y": f"legacy {source}", "k": kcal, "s": source})
            meal = c.execute(sa.text(
                "INSERT INTO custom_meal (user_id, meal_name, date_key) "
                "VALUES (:u, 'Saved', '2026-11-01') RETURNING id"), {"u": rich}).scalar()
            c.execute(sa.text(
                "INSERT INTO custom_meal_item (custom_meal_id, food_name, grams, calories, "
                "protein, carbs, fat) VALUES (:m, 'Oats', 80, 300, 10, 54, 6)"), {"m": meal})
            c.execute(sa.text(
                "INSERT INTO weekly_check_in (user_id, weight) VALUES (:u, 81.5)"), {"u": rich})
            # Narrow (pre-LAUNCH_FREE) scopes, as the live predicate allows.
            for scope, key, used in (("STARTER", "NEVER", 4), ("PREMIUM", "2026-11", 7)):
                c.execute(sa.text(
                    "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, "
                    "policy_version, granted, used, reserved, billed_releases, created_at, "
                    "updated_at) VALUES (:o, :s, 'coach', :k, 'launch-v1-usable-result', 60, "
                    ":u, 1, 0, now(), now())"), {"o": rich, "s": scope, "k": key, "u": used})
    finally:
        engine.dispose()

    # Production's completion floor is raised (R1); new claims exist after it.
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            completion_fence.raise_floor(connection, FLOOR_REVISION)
    finally:
        engine.dispose()
    for owner in (ids["r6_rich"], ids["r6_claim_only"]):
        _sql(url, "INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
                  "VALUES (:u, '2026-11-02', 'none', now())", u=owner)
    return ids


def _snapshot(url, tables=SEEDED, like=None):
    """Every row of every table. With ``like`` (an earlier snapshot), only the
    tables and columns that snapshot had, so added columns are excluded."""
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            snapshot = {}
            for table in (like if like is not None else tables):
                columns = (like[table][0] if like is not None
                           else sorted(c["name"] for c in inspector.get_columns(table)))
                quoted = ", ".join(f'"{name}"' for name in columns)
                rows = connection.execute(sa.text(f'SELECT {quoted} FROM "{table}"')).all()
                snapshot[table] = (columns, sorted(repr(tuple(row)) for row in rows))
            return snapshot
    finally:
        engine.dispose()


def _bucket_scope(url):
    return _sql(url, "SELECT pg_get_constraintdef(oid), convalidated FROM pg_catalog.pg_constraint "
                     "WHERE conname = 'ck_ai_allowance_bucket_scope'")[0]


def _has_tables(url, names):
    engine = _engine(url)
    try:
        inspector = sa.inspect(engine)
        return {name: inspector.has_table(name) for name in names}
    finally:
        engine.dispose()


def _verify_head_fence(url):
    """The installed fence must be the O2-B reviewed one, strictly verified."""
    import importlib.util
    path = ROOT / "migrations" / "versions" / "f01e2a5e0002_health_withdrawal_erasure.py"
    spec = importlib.util.spec_from_file_location("_r6_chain_o2b", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            module.verify_fence(connection)
            source = connection.execute(sa.text(
                "SELECT p.prosrc FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n "
                "ON n.oid = p.pronamespace WHERE n.nspname = 'public' "
                "AND p.proname = 'completion_fence_claim_retention'")).scalar()
    finally:
        engine.dispose()
    assert source == module.RETENTION_BODY


# ── 1. the graph (no database) ──────────────────────────────────────────────

def test_release_chain_is_exactly_the_four_reviewed_forward_migrations():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory(str(ROOT / "migrations"))
    assert script.get_heads() == [HEAD]
    walked = [rev.revision for rev in script.iterate_revisions(HEAD, LIVE_HEAD)]
    assert tuple(reversed(walked)) == CHAIN
    parent = LIVE_HEAD
    for revision in CHAIN:
        rev = script.get_revision(revision)
        assert rev.down_revision == parent and not rev.branch_labels and not rev.dependencies
        parent = revision
    # The completion fence the O2-B migration replaces is already live.
    live = {rev.revision for rev in script.iterate_revisions(LIVE_HEAD, "base")}
    assert "d7e3a9c1f5b8" in live and not live & set(CHAIN)


@pytest.fixture
def trees(tmp_path):
    """Candidate = the repository; previous = the repository at LIVE_HEAD."""
    previous_dir = tmp_path / "previous-migrations"
    shutil.copytree(ROOT / "migrations", previous_dir,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for path in (previous_dir / "versions").glob("*.py"):
        if path.name.split("_", 1)[0] in CHAIN:
            path.unlink()
    candidate = frozenset(tx._revisions_with_digests(ROOT / "migrations" / "versions", mec))
    previous = frozenset(tx._revisions_with_digests(previous_dir / "versions", mec))
    assert candidate - previous == set(CHAIN) and LIVE_HEAD in previous
    return {"previous_dir": previous_dir, "candidate": candidate, "previous": previous}


# ── 2. populated upgrade through the real release step ──────────────────────

def test_populated_live_head_reaches_head_without_touching_existing_rows(chain_url, monkeypatch):
    _upgrade_to(monkeypatch, LIVE_HEAD)
    assert _versions(chain_url) == [LIVE_HEAD]
    ids = _seed(chain_url)
    before = _snapshot(chain_url)
    assert len(before["workout_completion_day"][1]) == 2
    definition, valid = _bucket_scope(chain_url)
    assert valid is True and "LAUNCH_FREE" not in definition
    assert not any(_has_tables(chain_url, NEW_TABLES).values())

    result = _release_prepare(monkeypatch)
    assert result.exit_code == 0, result.output
    assert _versions(chain_url) == [HEAD]

    # Every pre-existing row is byte-for-byte the same on its original columns.
    assert _snapshot(chain_url, like=before) == before

    # Expand-only: provenance columns exist, nullable, never backfilled.
    for table, columns in PROVIDER_COLUMNS.items():
        rows = _sql(chain_url, f"SELECT {', '.join(columns)} FROM {table}")
        assert rows and all(value is None for row in rows for value in row)
    assert all(_has_tables(chain_url, NEW_TABLES).values())
    # Nothing retroactively authorized or erased; no retained totals invented.
    for table in ("health_consent_state", "health_consent_event", "health_erasure",
                  "health_legal_hold", "meal_log_retained_total"):
        assert _sql(chain_url, f"SELECT count(*) FROM {table}")[0][0] == 0, table
    definition, valid = _bucket_scope(chain_url)
    assert valid is True and "'LAUNCH_FREE'" in definition and "'STARTER'" in definition
    _verify_head_fence(chain_url)

    # O2-B retention: a live owner's claim is still undeletable without a
    # stopped erasure; account deletion (owner gone) still removes claims.
    rich, claim_only = ids["r6_rich"], ids["r6_claim_only"]
    assert _pgcode(lambda: _sql(
        chain_url, "DELETE FROM workout_completion_day WHERE user_id = :u", u=rich)) == "AXF05"
    _sql(chain_url, 'DELETE FROM "user" WHERE id = :u', u=claim_only)
    assert _sql(chain_url, "SELECT count(*) FROM workout_completion_day WHERE user_id = :u",
                u=claim_only)[0][0] == 0
    assert _sql(chain_url, "SELECT count(*) FROM workout_completion_day WHERE user_id = :u",
                u=rich)[0][0] == 1

    # Rerun is a no-op: same head, same rows.
    settled = _snapshot(chain_url, SEEDED + NEW_TABLES)
    rerun = _release_prepare(monkeypatch)
    assert rerun.exit_code == 0, rerun.output
    assert _versions(chain_url) == [HEAD]
    assert _snapshot(chain_url, SEEDED + NEW_TABLES) == settled
    _verify_head_fence(chain_url)


# ── 3. failure mid-chain is atomic; the rerun completes ─────────────────────

def _run_with_deadline(fn, deadline):
    outcome = {}

    def _target():
        started = time.monotonic()
        try:
            outcome["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome["error"] = exc
        outcome["elapsed"] = time.monotonic() - started

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(deadline)
    return thread, outcome


def test_a_lock_on_the_third_migration_rolls_back_the_whole_chain_then_rerun_completes(
        chain_url, monkeypatch, trees):
    _upgrade_to(monkeypatch, LIVE_HEAD)
    _seed(chain_url)
    before = _snapshot(chain_url)
    monkeypatch.setenv("FITX_MIGRATION_LOCK_TIMEOUT_MS", "700")

    # A serving-revision write transaction left open on meal_log: e1a5f0c3d2b4
    # needs ACCESS EXCLUSIVE there for ADD COLUMN, after d8f1/f01c already ran.
    blocker_engine = sa.create_engine(chain_url)
    blocker = blocker_engine.connect()
    blocker.execute(sa.text("UPDATE meal_log SET id = id WHERE false"))
    try:
        thread, outcome = _run_with_deadline(lambda: _release_prepare(monkeypatch), 90)
        if thread.is_alive():
            blocker.rollback()
            thread.join(30)
            pytest.fail("release-prepare waited on the lock with no bound")
        result = outcome.get("result")
        failed = outcome.get("error") is not None or (result is not None and result.exit_code != 0)
        assert failed, outcome
        assert outcome["elapsed"] < 60
    finally:
        blocker.rollback()
        blocker.close()
        blocker_engine.dispose()

    # ONE transaction: the two migrations that had already run are gone too.
    assert _versions(chain_url) == [LIVE_HEAD]
    assert not any(_has_tables(chain_url, NEW_TABLES).values())
    definition, _ = _bucket_scope(chain_url)
    assert "LAUNCH_FREE" not in definition
    assert _snapshot(chain_url) == before

    # The previous writer is still restartable (no post-migration evidence).
    with monkeypatch.context() as scoped:
        scoped.setattr("app.schema_safety.migrations_directory",
                       lambda app: str(trees["previous_dir"]))
        scoped.delenv("FITX_SKIP_DB_INIT", raising=False)
        scoped.setenv("FITX_STARTUP_MODE", "read-only")
        from app import create_app
        _dispose(create_app())

    monkeypatch.delenv("FITX_MIGRATION_LOCK_TIMEOUT_MS", raising=False)
    result = _release_prepare(monkeypatch)
    assert result.exit_code == 0, result.output
    assert _versions(chain_url) == [HEAD]
    assert _snapshot(chain_url, like=before) == before
    _verify_head_fence(chain_url)


# ── 4. the R6 forward-recovery gate with the true four-revision delta ───────

class _GateOps:
    def __init__(self, payload, trees):
        self.payload, self.trees = payload, trees

    def schema_heads(self, image_id, revision, timeout):
        assert image_id == "sha256:" + "c" * 64 and revision == CAND_SHA
        return self.payload

    def source_migration_revisions(self, which, timeout):
        return self.trees[which]


def _gate(payload, trees):
    transaction = tx.Transaction(_GateOps(payload, trees), deploy_sha=CAND_SHA,
                                 previous_commit=PREV_SHA,
                                 public_health_url="https://fitx.example/health",
                                 log=lambda message: None, mode="forward-recovery")
    transaction.candidate_image_id = "sha256:" + "c" * 64
    transaction._recovery_schema_gate(transaction._step_deadline("capability_admission"))


def _schema_heads(monkeypatch):
    import json
    tool = _boot_tool(monkeypatch)
    try:
        result = tool.test_cli_runner().invoke(args=["schema-heads"])
    finally:
        _dispose(tool)
    lines = [line for line in result.output.splitlines()
             if line.startswith(tx.SCHEMA_HEADS_PREFIX)]
    assert result.exit_code == 0 and len(lines) == 1, result.output
    return json.loads(lines[0][len(tx.SCHEMA_HEADS_PREFIX):])


@pytest.mark.parametrize("revision,admitted", [
    (LIVE_HEAD, False), (CHAIN[0], True), (CHAIN[2], True), (HEAD, True),
])
def test_forward_recovery_gate_admits_exactly_the_post_migration_states(
        chain_url, monkeypatch, trees, revision, admitted):
    _upgrade_to(monkeypatch, revision)
    payload = _schema_heads(monkeypatch)
    assert payload["current"] == [revision]
    if admitted:
        _gate(payload, trees)
    else:
        with pytest.raises(tx.StepFailed):
            _gate(payload, trees)


def test_after_the_chain_only_the_candidate_boots(chain_url, monkeypatch, trees):
    from app.schema_safety import SchemaNotReady
    _upgrade_to(monkeypatch, LIVE_HEAD)
    _seed(chain_url)
    assert _release_prepare(monkeypatch).exit_code == 0
    with monkeypatch.context() as scoped:
        scoped.setattr("app.schema_safety.migrations_directory",
                       lambda app: str(trees["previous_dir"]))
        scoped.delenv("FITX_SKIP_DB_INIT", raising=False)
        scoped.setenv("FITX_STARTUP_MODE", "read-only")
        from app import create_app
        with pytest.raises(SchemaNotReady) as refused:
            create_app()
    assert refused.value.reason == "unknown_revision"
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    from app import create_app
    candidate = create_app()
    try:
        assert candidate.test_client().get("/health").status_code == 200
    finally:
        _dispose(candidate)


# ── 5. rollback boundary: no downgrade once O2-B evidence exists ────────────

def test_downgrade_refuses_once_legal_hold_evidence_exists(chain_url, monkeypatch, caplog):
    import flask_migrate
    _upgrade_to(monkeypatch, LIVE_HEAD)
    ids = _seed(chain_url)
    assert _release_prepare(monkeypatch).exit_code == 0
    _sql(chain_url, "INSERT INTO health_legal_hold (owner_id, categories, matter_ref, created_at, "
                    "review_at) VALUES (:u, '[\"workouts\"]', 'r6-synthetic', now(), "
                    "now() + interval '30 days')", u=ids["r6_rich"])
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            # Flask-Migrate reports the migration's RuntimeError and exits 1,
            # exactly as `flask db downgrade` would for an operator.
            with pytest.raises(SystemExit) as stopped:
                flask_migrate.downgrade(revision=LIVE_HEAD)
    finally:
        _dispose(tool)
    assert stopped.value.code == 1
    assert "refuses to erase consent or erasure evidence" in caplog.text
    assert _versions(chain_url) == [HEAD]
    _verify_head_fence(chain_url)
