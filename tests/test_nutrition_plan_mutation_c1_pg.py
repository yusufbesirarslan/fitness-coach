"""Adaptive Coach C1 targeted Nutrition edits on real PostgreSQL.

SQLite serializes writers, so it cannot prove any of this. The REAL D1 chain
runs: synthetic disclosures on a disposable database, a real affirmative grant,
``ai_consent.begin_operation`` and the real ``ai_consent.fence`` under the
owner lock. Only the meal estimator is injected (no provider I/O).

Interleavings are forced, never slept for: a holder parks owning the owner
row lock and the test proceeds only once PostgreSQL reports the contender
waiting on a lock (non-vacuity: the race really happened).

  P01 the same prepared edit applied twice at once writes once (replay)
  P02 two different edits from one base: exactly one wins, zero partial writes
  P03 an edit waiting on the owner lock goes stale when the holder edits
  P04 an edit waiting on the owner lock goes stale when a full replacement lands
  P05 concurrent undos: one exact-byte restore, the other refused
  P06 consent revoked (or revoked and regranted) after prepare: zero writes
  P07 an authority for another feature cannot write a Nutrition edit
  P08 a failed or partial estimate writes nothing
  P09 a plan written by the previous revision (no lineage, server default
      version) is editable and exactly undoable; its projection is unlabelled
  P10 the real migration keeps an existing plan byte-identical, and its
      downgrade refuses once edit history exists
"""
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

from tests.test_lp21_d1_operation_fencing_pg import consent, disclose, regrant  # noqa: E402
from tests.test_mobile_training_generation_pg import pg_generation_app  # noqa: E402,F401
from tests.test_r6_dual_revision_pg import _boot_tool, _dispose, pg_url  # noqa: E402,F401

WAIT = 15
PLAN = {
    "isim": "Plan A",
    "kahvalti": {"yemekler": ["Yumurta - 3 adet", "Tam buğday ekmeği - 2 dilim"],
                 "kalori": 420, "protein": 28, "karb": 35, "yag": 18},
    "ogle": {"yemekler": ["Tavuk göğsü - 150 g", "Pilav - 1 porsiyon"],
             "kalori": 600, "protein": 50, "karb": 60, "yag": 12},
    "toplam_kalori": 1020, "toplam_protein": 78, "toplam_karb": 95, "toplam_yag": 30,
}
# Deliberately not the canonical serialization: undo must return THESE bytes.
STORED = json.dumps(PLAN, ensure_ascii=True, indent=1)
OTHER_PLAN = dict(PLAN, isim="Plan B")
TABLE = {
    "tam buğday ekmeği - 2 dilim": {"calories": 160, "protein": 8, "carbs": 30, "fat": 1},
    "yumurta - 3 adet": {"calories": 210, "protein": 18, "carbs": 2, "fat": 15},
    "dana eti - 150 g": {"calories": 300, "protein": 39, "carbs": 0, "fat": 16},
    "avokado - yarım": {"calories": 120, "protein": 1.5, "carbs": 6, "fat": 10.5},
}


def estimator(table=TABLE):
    def run(names):
        return {n: table[n.casefold()] for n in names if n.casefold() in table}
    return run


def _npm():
    from app.services import nutrition_plan_mutation as npm
    return npm


def replace_cmd():
    return _npm().ReplaceMealItemCommand("kahvalti", "Yumurta - 3 adet", "Dana eti - 150 g")


def add_cmd():
    return _npm().AddMealItemCommand("kahvalti", "Avokado - yarım")


def ctx(key):
    npm = _npm()
    return npm.NutritionEditContext(idempotency_key=key, actor=npm.ACTOR_AI_COACH)


def lock_waiters():
    engine = sa.create_engine(os.environ["PG_TEST_DATABASE_URL"], poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            return conn.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'")).scalar_one()
    finally:
        engine.dispose()


def await_lock_waiter():
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        if lock_waiters():
            return
        time.sleep(0.02)
    raise AssertionError("no contender ever waited on the owner lock")


class Worker(threading.Thread):
    """Runs ``fn`` in its own app context; keeps the result or the exception."""

    def __init__(self, app, fn, name):
        super().__init__(name=name, daemon=True)
        self.app, self.fn, self.result, self.error = app, fn, None, None

    def run(self):
        from app.extensions import db
        with self.app.app_context():
            try:
                self.result = self.fn()
            except BaseException as exc:  # noqa: BLE001 - asserted by the test
                self.error = exc
            finally:
                db.session.remove()

    def finish(self):
        self.join(WAIT)
        assert not self.is_alive(), f"{self.name} did not finish"
        return self


@pytest.fixture
def c1(pg_generation_app):
    from d1_screened_support import screen_adult_tr
    from app.extensions import db
    from app.models import NutritionPlan
    app, owners, monkeypatch = pg_generation_app
    with app.app_context():
        screen_adult_tr(*owners)
    policies = disclose(monkeypatch)
    nutrition = policies["nutrition_plan"]
    with app.app_context():
        for owner in owners:
            consent(owner, nutrition, "affirmative_grant", f"grant-c1-{owner:04d}")
            db.session.add(NutritionPlan(user_id=owner, plan_data=STORED, score=8.0))
        db.session.commit()
    return SimpleNamespace(app=app, owner=owners[0], other=owners[1],
                           policy=nutrition, monkeypatch=monkeypatch)


def authority(owner, feature="nutrition_plan"):
    from app.services import ai, ai_consent
    return ai_consent.begin_operation(owner, feature, "openai", ai.OPENAI_MODEL)


auth_for = authority  # equal (owner, feature, generation, version) = the same operation


def prepare(owner, command, table=TABLE):
    return _npm().prepare_meal_edit(owner, command, estimator(table), authority=auth_for(owner))


def state(owner):
    from app.extensions import db
    from app.models import NutritionPlan, NutritionPlanMutationRecord
    db.session.expire_all()
    row = NutritionPlan.query.filter_by(user_id=owner).one()
    count = NutritionPlanMutationRecord.query.filter_by(user_id=owner).count()
    result = SimpleNamespace(data=row.plan_data, version=row.mutation_version,
                             lineage=row.lineage_id, row_id=row.id, journal=count)
    db.session.rollback()
    return result


def serialized(document):
    return json.dumps(document, ensure_ascii=False)


# ── P01 / P02 ───────────────────────────────────────────────────────────────


def test_p01_the_same_edit_applied_twice_at_once_writes_once(c1):
    app, owner = c1.app, c1.owner
    with app.app_context():
        prepared = prepare(owner, replace_cmd())
        auth = authority(owner)
    barrier = threading.Barrier(2)

    def apply():
        barrier.wait(timeout=WAIT)
        return _npm().apply_prepared_edit(owner, prepared, ctx("same-key-0001"), authority=auth)

    workers = [Worker(app, apply, f"apply-{i}") for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.finish()

    assert [w.error for w in workers] == [None, None]
    results = [w.result for w in workers]
    assert {r.mutation_id for r in results} == {results[0].mutation_id}
    assert sorted(r.replayed for r in results) == [False, True]
    assert {r.plan_version for r in results} == {1}
    with app.app_context():
        after = state(owner)
        assert (after.version, after.journal) == (1, 1)
        assert after.data == serialized(prepared.document)
        assert state(c1.other).data == STORED  # the other account is untouched


def test_p02_two_edits_from_one_base_exactly_one_wins(c1):
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        first, second = prepare(owner, replace_cmd()), prepare(owner, add_cmd())
        auth = authority(owner)
    barrier = threading.Barrier(2)

    def apply(prepared, key):
        def run():
            barrier.wait(timeout=WAIT)
            return npm.apply_prepared_edit(owner, prepared, ctx(key), authority=auth)
        return run

    workers = [Worker(app, apply(first, "edit-key-0001"), "first"),
               Worker(app, apply(second, "edit-key-0002"), "second")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.finish()

    winners = [w for w in workers if w.error is None]
    losers = [w for w in workers if w.error is not None]
    assert len(winners) == 1 and len(losers) == 1
    assert isinstance(losers[0].error, npm.NutritionPlanStateConflict)
    won = first if winners[0].name == "first" else second
    with app.app_context():
        after = state(owner)
        assert (after.version, after.journal) == (1, 1)
        assert after.data == serialized(won.document)


# ── P03 / P04: forced interleavings on the owner lock ──────────────────────


def _hold_owner_lock_then(app, owner, then):
    """A holder takes the owner lock, waits for a contender, then runs ``then``."""
    go = threading.Event()
    held = threading.Event()

    def holder():
        from app.services.plan_owner_lock import lock_plan_owner
        lock_plan_owner(owner)
        held.set()
        assert go.wait(WAIT)
        return then()  # re-locking in the same transaction is a no-op

    return Worker(app, holder, "holder"), held, go


def test_p03_an_edit_waiting_on_the_lock_goes_stale_when_the_holder_edits(c1):
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        first, second = prepare(owner, replace_cmd()), prepare(owner, add_cmd())
        auth = authority(owner)

    holder, held, go = _hold_owner_lock_then(app, owner, lambda: npm.apply_prepared_edit(
        owner, first, ctx("edit-key-0001"), authority=auth))
    holder.start()
    assert held.wait(WAIT)
    contender = Worker(app, lambda: npm.apply_prepared_edit(
        owner, second, ctx("edit-key-0002"), authority=auth), "contender")
    contender.start()
    await_lock_waiter()  # non-vacuity: the contender is blocked on the owner row
    go.set()
    holder.finish()
    contender.finish()

    assert holder.error is None and holder.result.outcome == npm.OUTCOME_APPLIED
    assert isinstance(contender.error, npm.NutritionPlanStateConflict)
    with app.app_context():
        after = state(owner)
        assert (after.version, after.journal) == (1, 1)
        assert after.data == serialized(first.document)


def test_p04_an_edit_waiting_on_the_lock_goes_stale_on_full_replacement(c1):
    app, owner = c1.app, c1.owner
    npm = _npm()
    from app.services.nutrition_plan_schema import validate_nutrition_plan_for_save
    from app.services.nutrition_plan_store import replace_nutrition_plan
    with app.app_context():
        prepared = prepare(owner, replace_cmd())
        auth = authority(owner)
        base = state(owner)

    holder, held, go = _hold_owner_lock_then(app, owner, lambda: replace_nutrition_plan(
        owner, validate_nutrition_plan_for_save(OTHER_PLAN), 7.0, authority=auth).id)
    holder.start()
    assert held.wait(WAIT)
    contender = Worker(app, lambda: npm.apply_prepared_edit(
        owner, prepared, ctx("edit-key-0001"), authority=auth), "contender")
    contender.start()
    await_lock_waiter()
    go.set()
    holder.finish()
    contender.finish()

    assert holder.error is None
    assert isinstance(contender.error, npm.NutritionPlanStateConflict)
    with app.app_context():
        after = state(owner)
        assert after.row_id == holder.result and after.row_id != base.row_id
        assert after.lineage != base.lineage  # a replacement starts a new lineage
        assert json.loads(after.data)["isim"] == "Plan B"
        assert (after.version, after.journal) == (0, 0)
        # Nothing to undo across the replacement.
        with pytest.raises(npm.NutritionUndoUnavailable):
            npm.undo_last_nutrition_edit(owner, ctx("undo-key-0001"))


# ── P05: undo ───────────────────────────────────────────────────────────────


def test_p05_concurrent_undos_restore_the_exact_bytes_once(c1):
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        npm.apply_prepared_edit(owner, prepare(owner, replace_cmd()), ctx("edit-key-0001"),
                                authority=authority(owner))
    barrier = threading.Barrier(3)

    def undo(key):
        def run():
            barrier.wait(timeout=WAIT)
            return npm.undo_last_nutrition_edit(owner, ctx(key))
        return run

    # Two distinct undos and one retry of the first under the same key.
    workers = [Worker(app, undo("undo-key-0001"), "undo-a"),
               Worker(app, undo("undo-key-0002"), "undo-b"),
               Worker(app, undo("undo-key-0001"), "undo-a-retry")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.finish()

    by_name = {w.name: w for w in workers}
    a, b, retry = by_name["undo-a"], by_name["undo-b"], by_name["undo-a-retry"]
    if a.error is None:
        # Key 0001 won: its retry replays, the other key finds nothing to undo.
        assert retry.error is None and retry.result.mutation_id == a.result.mutation_id
        assert isinstance(b.error, npm.NutritionUndoUnavailable)
    else:
        # Key 0002 won: both key-0001 calls find nothing to undo.
        assert b.error is None
        assert isinstance(a.error, npm.NutritionUndoUnavailable)
        assert isinstance(retry.error, npm.NutritionUndoUnavailable)
    with app.app_context():
        after = state(owner)
        assert after.data == STORED  # exact original bytes, not a re-serialization
        assert (after.version, after.journal) == (2, 2)


# ── P06 / P07: consent boundary ─────────────────────────────────────────────


def test_p06_consent_revoked_or_regranted_after_prepare_writes_nothing(c1):
    from app.services.ai_consent import ConsentDenied
    app, owner, policy = c1.app, c1.owner, c1.policy
    npm = _npm()
    with app.app_context():
        prepared = prepare(owner, replace_cmd())
        auth = authority(owner)
        consent(owner, policy, "revoke", "revoke-c1-0001")
        with pytest.raises(ConsentDenied):
            npm.apply_prepared_edit(owner, prepared, ctx("edit-key-0001"), authority=auth)
        assert (state(owner).version, state(owner).journal, state(owner).data) == (0, 0, STORED)

        consent(owner, policy, "affirmative_grant", "regrant-c1-0001")
        # The ORIGINAL authority stays dead after a regrant.
        with pytest.raises(ConsentDenied):
            npm.apply_prepared_edit(owner, prepared, ctx("edit-key-0001"), authority=auth)
        assert (state(owner).version, state(owner).journal, state(owner).data) == (0, 0, STORED)

        # Nor does a fresh operation revive the pre-revoke estimate: the edit
        # is bound to the operation it was estimated under (LP21).
        with pytest.raises(ConsentDenied):
            npm.apply_prepared_edit(owner, prepared, ctx("edit-key-0001"),
                                    authority=authority(owner))
        assert (state(owner).version, state(owner).journal, state(owner).data) == (0, 0, STORED)

        # Only a NEW estimate, prepared under the new operation, can be applied.
        fresh = authority(owner)
        again = npm.prepare_meal_edit(owner, replace_cmd(), estimator(), authority=fresh)
        result = npm.apply_prepared_edit(owner, again, ctx("edit-key-0002"), authority=fresh)
        assert result.outcome == npm.OUTCOME_APPLIED
        assert (state(owner).version, state(owner).journal) == (1, 1)


def test_p06b_regrant_between_prepare_and_apply_also_refuses_the_old_operation(c1):
    from app.services.ai_consent import ConsentDenied
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        prepared = prepare(owner, replace_cmd())
        auth = authority(owner)
        regrant(owner, c1.policy, "c1-0002")
        with pytest.raises(ConsentDenied):
            npm.apply_prepared_edit(owner, prepared, ctx("edit-key-0001"), authority=auth)
        assert (state(owner).version, state(owner).journal, state(owner).data) == (0, 0, STORED)


def test_p07_another_feature_s_or_account_s_authority_cannot_write(c1):
    from app.services import ai_consent
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        prepared = prepare(owner, replace_cmd())
        real = authority(owner)
        foreign = [
            ai_consent.OperationAuthority(owner, "training_plan", real.generation, real.version),
            ai_consent.OperationAuthority(owner, "coach", real.generation, real.version),
            authority(c1.other),  # a real authority, for the other account
            None,
        ]
        for index, auth in enumerate(foreign):
            with pytest.raises(ai_consent.ConsentDenied):
                npm.apply_prepared_edit(owner, prepared, ctx(f"edit-key-{index:04d}"),
                                        authority=auth)
        assert (state(owner).version, state(owner).journal, state(owner).data) == (0, 0, STORED)


# ── P08: failed estimates ───────────────────────────────────────────────────


def test_p08_a_failed_or_partial_estimate_writes_nothing(c1):
    app, owner = c1.app, c1.owner
    npm = _npm()

    def boom(names):
        raise RuntimeError("provider timeout")

    partial = {k: v for k, v in TABLE.items() if k != "dana eti - 150 g"}
    implausible = dict(TABLE, **{"dana eti - 150 g": {
        "calories": 2000, "protein": 1, "carbs": 1, "fat": 1}})
    with app.app_context():
        before = state(owner)
        for estimate in (boom, estimator(partial), estimator(implausible), estimator({})):
            with pytest.raises(npm.EstimateUnavailable):
                npm.prepare_meal_edit(owner, replace_cmd(), estimate,
                                      authority=authority(owner))
        # An unresolvable target never reaches the estimator or the plan
        # (ambiguity itself is covered on SQLite: it needs a doctored plan).
        with pytest.raises(npm.MealItemNotFound):
            npm.prepare_meal_edit(owner, npm.RemoveMealItemCommand("kahvalti", "e"),
                                  estimator(), authority=authority(owner))
        after = state(owner)
        assert (after.data, after.version, after.journal, after.lineage) == (
            STORED, 0, 0, before.lineage)


# ── P09 / P10: existing plans ───────────────────────────────────────────────


def test_p09_a_plan_written_by_the_previous_revision_is_editable_and_undoable(c1):
    from app.extensions import db
    from app.models import NutritionPlan
    from app.services.nutrition_native.plan import project_plan_row
    app, owner = c1.app, c1.owner
    npm = _npm()
    with app.app_context():
        NutritionPlan.query.filter_by(user_id=owner).delete()
        # Exactly the columns the pre-C1 ORM inserted.
        db.session.execute(sa.text(
            "INSERT INTO nutrition_plan (user_id, plan_data, score, created_at) "
            "VALUES (:u, :d, 8.0, now())"), {"u": owner, "d": STORED})
        db.session.commit()
        legacy = state(owner)
        assert (legacy.lineage, legacy.version) == (None, 0)
        row = NutritionPlan.query.filter_by(user_id=owner).one()
        projected = project_plan_row(row, "disposable-secret")
        db.session.rollback()
        assert projected["state"] == "active" and len(projected["meals"]) == 2
        assert [meal["estimated"] for meal in projected["meals"]] == [False, False]

        npm.apply_prepared_edit(owner, prepare(owner, replace_cmd()), ctx("edit-key-0001"),
                                authority=authority(owner))
        edited = state(owner)
        assert edited.lineage is not None and edited.version == 1
        row = NutritionPlan.query.filter_by(user_id=owner).one()
        labels = {m["slot"]: m["estimated"]
                  for m in project_plan_row(row, "disposable-secret")["meals"]}
        db.session.rollback()
        assert labels == {"kahvalti": True, "ogle": False}  # only the re-estimated meal
        npm.undo_last_nutrition_edit(owner, ctx("undo-key-0001"))
        undone = state(owner)
        assert (undone.data, undone.version, undone.lineage) == (STORED, 2, edited.lineage)


def _sql(url, statement, **params):
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as connection:
            result = connection.execute(sa.text(statement), params)
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _migrate(monkeypatch, action, revision):
    import flask_migrate
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            getattr(flask_migrate, action)(revision=revision)
    finally:
        _dispose(tool)


def test_p10_the_migration_keeps_existing_plans_and_refuses_a_lossy_downgrade(
        pg_url, monkeypatch):
    _migrate(monkeypatch, "upgrade", "ad17de1e0001")
    owner = _sql(pg_url,
                 'INSERT INTO "user" (username, email, cognito_sub, referral_code) '
                 "VALUES ('c1mig', 'c1mig@example.com', 'sub-c1mig', 'C1MIG0001') "
                 "RETURNING id")[0][0]
    _sql(pg_url, "INSERT INTO nutrition_plan (user_id, plan_data, score, created_at) "
                 "VALUES (:u, :d, 8.0, now())", u=owner, d=STORED)

    _migrate(monkeypatch, "upgrade", "c1a0d2e3f4a5")
    rows = _sql(pg_url, "SELECT plan_data, lineage_id, mutation_version FROM nutrition_plan "
                        "WHERE user_id = :u", u=owner)
    assert rows == [(STORED, None, 0)]  # byte-identical, no backfill
    assert _sql(pg_url, "SELECT count(*) FROM nutrition_plan_mutation_record") == [(0,)]

    # An empty ledger downgrades cleanly and the plan survives unchanged.
    _migrate(monkeypatch, "downgrade", "ad17de1e0001")
    assert _sql(pg_url, "SELECT plan_data FROM nutrition_plan WHERE user_id = :u",
                u=owner) == [(STORED,)]

    _migrate(monkeypatch, "upgrade", "c1a0d2e3f4a5")
    _sql(pg_url,
         "INSERT INTO nutrition_plan_mutation_record (public_id, user_id, plan_lineage_id, "
         "operation_kind, command_type, command_fingerprint, actor, outcome, before_version, "
         "after_version, before_snapshot, after_snapshot, before_fingerprint, "
         "after_fingerprint, idempotency_key, created_at) VALUES ('m-1', :u, 'l-1', 'edit', "
         "'nutrition.replace_meal_item', 'f', 'user', 'applied', 0, 1, :d, :d, 'a', 'b', "
         "'edit-key-0001', now())", u=owner, d=STORED)
    # flask-migrate logs the migration's RuntimeError and exits non-zero.
    with pytest.raises(SystemExit):
        _migrate(monkeypatch, "downgrade", "ad17de1e0001")
    assert _sql(pg_url, "SELECT count(*) FROM nutrition_plan_mutation_record") == [(1,)]
    assert _sql(pg_url, "SELECT version_num FROM alembic_version") == [("c1a0d2e3f4a5",)]
    assert _sql(pg_url, "SELECT plan_data, mutation_version FROM nutrition_plan "
                        "WHERE user_id = :u", u=owner) == [(STORED, 0)]
