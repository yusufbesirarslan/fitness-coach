"""Opt-in PostgreSQL races for destructive training-plan replacement.

SQLite cannot arbitrate any of this: it serializes writers at the file level, so
a green run there says nothing about two workers reaching one plan row on
PostgreSQL. These run against a disposable database and are collected by CI's
``mobile-pg-concurrency`` job.

There are TWO kinds of test here, and the difference matters.

The free-running cases release their contenders at a ``threading.Barrier`` and
assert an *outcome* invariant: exactly one replacement survives, a Coach
mutation is never silently discarded, a concurrent create never overwrites, a
stale expectation performs zero destructive writes. They do not pin down which
lock settled a given run, because asserting the outcome freezes the guarantee
while asserting the mechanism would freeze an implementation detail.

But a barrier only starts two threads together; it cannot make them *interleave*
at the one instruction that matters, and with a GIL one thread usually runs away
with the whole operation. Deleting either lock from the boundary left every
free-running case above green — so on their own they would have made "the
comparison happens under a lock" an untested claim. The two LOCK PROBE tests
close that: they force the window open (pausing one caller between its locked
read and its destructive write) and assert directly that the other caller cannot
cross it. One probe covers the plan-row lock (a Coach mutation), the other the
owner-row lock (a second create) — the only case a plan-row lock cannot reach,
because a user with no plan has no row to lock.

There is still no ``sleep()`` here. The probes wait on an ``Event`` with a
timeout, and the two outcomes they separate differ by orders of magnitude: an
uncontended local write returns in milliseconds, while one blocked on a held
``FOR UPDATE`` cannot return until the holding transaction commits.
"""
import json
import os
import threading

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


# How long a LOCK PROBE holds the critical section open while asking whether the
# other caller can cross it. It only has to be long enough that "did not return"
# means "is blocked" rather than "was slow" — see the module docstring.
LOCK_PROBE_SECONDS = 3.0


PROGRAM = (
    '{"program": ['
    '{"gun": "Pazartesi", "tip": "antrenman", "odak": "Itis", '
    '"egzersizler": [{"isim": "Bench Press", "set": 3, "tekrar": "8-12"}]}, '
    '{"gun": "Carsamba", "tip": "antrenman", "odak": "Cekis", '
    '"egzersizler": [{"isim": "Barbell Row", "set": 4, "tekrar": "6-10"}]}'
    '], "haftalik_ozet": {"yogunluk_skoru": 7}}'
)


def _proposal(marker):
    """A distinct, parseable replacement document, tagged so the winner is
    identifiable in the final state."""
    return json.dumps({
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": marker,
             "egzersizler": [
                 {"isim": "Overhead Press", "set": 3, "tekrar": "8-12"}]},
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7},
    }, ensure_ascii=False)


@pytest.fixture
def pg_app():
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from app.models import PlanMutationRecord, TrainingPlan, User

    app = Flask("plan-replacement-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-plan-replacement-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        # Tripwire, not decoration. Every case here asserts an exact final row
        # count and an exact version, so a row surviving from the previous test
        # would not fail loudly — it would quietly change what "the current
        # plan" is and make a race test report a conclusion about the wrong
        # data. Prove the owner starts empty.
        assert TrainingPlan.query.count() == 0, "leftover plan rows"
        assert PlanMutationRecord.query.count() == 0, "leftover journal rows"
        user = User(username="pg-replace", email="pg-replace@example.invalid",
                    cognito_sub="pg-replace-sub")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    try:
        yield app, user_id
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


@pytest.fixture
def seeded(pg_app):
    """A user holding one plan, plus that plan's canonical freshness identity."""
    from app.extensions import db
    from app.models import TrainingPlan

    app, user_id = pg_app
    with app.app_context():
        plan = TrainingPlan(user_id=user_id, plan_data=PROGRAM, score=8)
        db.session.add(plan)
        db.session.commit()
        db.session.remove()
    identity = _state(app, user_id)["active"][:2]
    return app, user_id, identity


def _state(app, user_id):
    """Every plan row this owner holds, read fresh from the database.

    ``remove()`` + ``populate_existing()`` are not superstition. These tests
    assert exact versions, and the ORM will hand back an identity-mapped
    instance carrying the values it was first loaded with — so a verdict built
    on an unrefreshed read could report "version 0" about a row another
    transaction has already moved. The evidence has to come from the database,
    not from a cache of it.
    """
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services.today_facts import get_active_plan

    with app.app_context():
        db.session.remove()
        rows = (TrainingPlan.query.filter_by(user_id=user_id)
                .populate_existing().all())
        # WHICH row is active stays the canonical selector's call, never a
        # second opinion invented here; the fresh session above is what makes
        # the instance it returns current rather than remembered.
        active = get_active_plan(user_id)
        state = {
            "count": len(rows),
            "lineages": {row.lineage_id for row in rows},
            "active": None if active is None else (
                active.lineage_id, active.mutation_version, active.plan_data),
        }
        db.session.remove()
        return state


def _run(app, calls):
    """Release ``len(calls)`` threads at one barrier and collect their outcomes."""
    from app.extensions import db
    from app.services.plan_mutation import PlanNotFound, PlanStateConflict
    from app.services.plan_replacement import PlanReplacementConflict

    barrier = threading.Barrier(len(calls))
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                outcomes[index] = ("ok", calls[index]())
            except PlanReplacementConflict as error:
                outcomes[index] = ("conflict", error.reason)
            except PlanNotFound:
                outcomes[index] = ("plan_not_found", None)
            except PlanStateConflict:
                outcomes[index] = ("plan_state_conflict", None)
            except Exception as error:  # pragma: no cover - surfaced by asserts
                outcomes[index] = ("unexpected", type(error).__name__, str(error))
            finally:
                db.session.remove()

    threads = [threading.Thread(target=contender, args=(index,), daemon=True)
               for index in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), outcomes
    return outcomes


def _replace(user_id, identity, marker):
    from app.services.plan_replacement import (
        PlanExpectation, replace_training_plan,
    )

    expectation = (
        PlanExpectation(absent=True) if identity is None
        else PlanExpectation(absent=False, lineage_id=identity[0],
                             mutation_version=identity[1])
    )
    return lambda: replace_training_plan(
        user_id, expectation, plan_data=_proposal(marker), score=9)


def _coach_mutation(user_id, key):
    from app.services.plan_mutation import (
        MutationContext, ReplaceExerciseCommand, apply_plan_mutation,
    )

    return lambda: apply_plan_mutation(
        user_id,
        ReplaceExerciseCommand(day="Pazartesi", exercise="Bench Press",
                               replacement="Machine Press"),
        MutationContext(idempotency_key=key))


# ── CASE A — two replacements from one expected version ──────────────────────

def test_two_replacements_from_one_version_produce_exactly_one_winner(seeded):
    """The headline race.

    Two clients each generated a proposal against (L, V) and save at the same
    instant. Before the precondition, both deleted and both inserted and the
    survivor was whichever transaction committed last — one user's plan simply
    vanished. Now exactly one may proceed.
    """
    app, user_id, identity = seeded

    outcomes = _run(app, [_replace(user_id, identity, "alpha"),
                          _replace(user_id, identity, "beta")])

    kinds = sorted(outcome[0] for outcome in outcomes.values())
    assert kinds == ["conflict", "ok"], outcomes
    loser = next(o for o in outcomes.values() if o[0] == "conflict")
    # The loser is refused deterministically, and it is refused for a REASON
    # from the closed vocabulary — never a database error surfaced as one.
    assert loser[1] in {"lineage_mismatch", "version_mismatch",
                        "expected_present_but_absent", "plan_appeared"}

    state = _state(app, user_id)
    # One plan, one lineage: no lost row, no orphan, no merged hybrid.
    assert state["count"] == 1
    assert identity[0] not in state["lineages"]
    lineage, version, plan_data = state["active"]
    assert version == 0
    winner = next(o for o in outcomes.values() if o[0] == "ok")[1]
    assert lineage == winner.lineage_id
    # Exactly one proposal survives, whole. Not a blend of the two.
    markers = [m for m in ("alpha", "beta") if m in plan_data]
    assert markers == [winner_marker(plan_data)]


def winner_marker(plan_data):
    return "alpha" if "alpha" in plan_data else "beta"


# ── CASE B — Coach mutation versus whole replacement ─────────────────────────

def test_a_coach_mutation_is_never_silently_overwritten(seeded):
    """Both start from (L, V). Whatever the interleaving, the final state must
    agree with what each caller was TOLD.

    The forbidden outcome is the one that used to happen: the Coach reports a
    persisted change, the replacement also reports success, and the change is
    gone.
    """
    app, user_id, identity = seeded

    outcomes = _run(app, [_replace(user_id, identity, "replacement"),
                          _coach_mutation(user_id, "pg-race-b-0001")])
    replacement, coach = outcomes[0], outcomes[1]
    state = _state(app, user_id)
    # Every assertion below reports the outcomes AND the state they disagree
    # with; a bare "1 != 0" cannot be diagnosed after the fact.
    outcomes = {"outcomes": outcomes, "state": state, "seed": identity}
    assert state["count"] == 1, outcomes
    lineage, version, plan_data = state["active"]

    if replacement[0] == "ok":
        # The replacement won the row. Its lineage is what exists now.
        assert lineage == replacement[1].lineage_id, outcomes
        assert lineage != identity[0]
        if coach[0] == "ok":
            # The Coach ran AFTER, against the new plan, and says so.
            assert coach[1].plan_version == version == 1, outcomes
            assert "Machine Press" in plan_data
        else:
            # Or it was refused deterministically. It must NOT have claimed a
            # change that no longer exists.
            assert coach[0] in {"plan_not_found", "plan_state_conflict"}, outcomes
            assert version == 0
            assert "Machine Press" not in plan_data
    else:
        # The Coach won. The replacement must have been refused, and the
        # Coach's persisted change must still be there.
        assert replacement[0] == "conflict", outcomes
        assert replacement[1] == "version_mismatch", outcomes
        assert coach[0] == "ok", outcomes
        assert lineage == identity[0]
        assert version == 1
        assert "Machine Press" in plan_data
        assert "replacement" not in plan_data


def test_the_comparison_is_made_under_a_lock_the_coach_cannot_cross(
        seeded, monkeypatch):
    """The lock itself, pinned deterministically.

    The free-running CASE B above asserts the right invariant but cannot
    guarantee it ever reaches the dangerous interleaving — with two threads and
    a GIL, one usually runs away with the whole operation. Removing the row lock
    from the replacement therefore left every other test in this file green,
    which would have made "the comparison happens under the lock" an untested
    claim.

    So the interleaving is forced. The replacement is paused between its locking
    read and its destructive write, and a Coach mutation is released into that
    exact window. If the comparison is genuinely made under a lock the Coach
    contends for, the Coach CANNOT commit inside the window; it blocks, the
    replacement completes, and the Coach then discovers its target is gone. If
    the comparison is made on an unlocked read, the Coach sails through, commits
    a change the replacement then deletes, and both callers are told they
    succeeded — the exact lost update this prerequisite exists to remove.
    """
    from app.extensions import db
    from app.services import plan_replacement
    from app.services.plan_mutation import PlanNotFound, PlanStateConflict

    app, user_id, identity = seeded
    compared = threading.Event()
    coach_returned = threading.Event()
    observed = {}

    real_get_active_plan = plan_replacement.get_active_plan

    def paused_selector(uid):
        # Called immediately after the locking read, so every lock this
        # transaction takes is already held here.
        plan = real_get_active_plan(uid)
        compared.set()
        observed["coach_escaped"] = coach_returned.wait(LOCK_PROBE_SECONDS)
        return plan

    # Installed through ``monkeypatch``, in the main thread, so pytest owns the
    # undo. A hand-rolled restore inside a worker thread would leave a paused
    # selector installed for the rest of the module if that thread ever died
    # before its ``finally`` — and the next test would then be measuring this
    # one's plumbing.
    monkeypatch.setattr(plan_replacement, "get_active_plan", paused_selector)

    def replacement():
        with app.app_context():
            try:
                observed["replacement"] = ("ok", _replace(
                    user_id, identity, "replacement")())
            except plan_replacement.PlanReplacementConflict as error:
                observed["replacement"] = ("conflict", error.reason)
            except Exception as error:  # pragma: no cover - surfaced by asserts
                observed["replacement"] = ("unexpected", type(error).__name__)
            finally:
                compared.set()  # never strand the other thread
                db.session.remove()

    def coach():
        with app.app_context():
            compared.wait(timeout=10)
            try:
                observed["coach"] = ("ok", _coach_mutation(
                    user_id, "pg-lock-probe-0001")())
            except (PlanNotFound, PlanStateConflict) as error:
                observed["coach"] = ("refused", type(error).__name__)
            except Exception as error:  # pragma: no cover - surfaced by asserts
                observed["coach"] = ("unexpected", type(error).__name__)
            finally:
                coach_returned.set()
                db.session.remove()

    threads = [threading.Thread(target=replacement, daemon=True),
               threading.Thread(target=coach, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), observed

    # THE assertion: a Coach mutation could not commit while the replacement
    # held its critical section open.
    assert observed["coach_escaped"] is False, observed
    assert observed["replacement"][0] == "ok", observed
    assert observed["coach"][0] == "refused", observed

    state = _state(app, user_id)
    assert state["count"] == 1, observed
    lineage, version, plan_data = state["active"]
    assert lineage == observed["replacement"][1].lineage_id
    assert version == 0
    # And nothing claims a change that is not there.
    assert "Machine Press" not in plan_data
    assert "replacement" in plan_data


# ── CASE C — two creates against an empty owner ──────────────────────────────

def test_two_concurrent_creates_produce_exactly_one_plan(pg_app):
    """A no-plan page must not overwrite a plan created a moment ago.

    Both callers assert "I expect no active plan". With no row to lock, the
    owner row is what serializes them; the loser then SEES the winner's plan
    and is refused rather than deleting it.
    """
    app, user_id = pg_app

    outcomes = _run(app, [_replace(user_id, None, "alpha"),
                          _replace(user_id, None, "beta")])

    kinds = sorted(outcome[0] for outcome in outcomes.values())
    assert kinds == ["conflict", "ok"], outcomes
    loser = next(o for o in outcomes.values() if o[0] == "conflict")
    assert loser[1] == "expected_absent_but_plan_exists", outcomes

    state = _state(app, user_id)
    assert state["count"] == 1, outcomes
    _lineage, version, plan_data = state["active"]
    assert version == 0
    assert len([m for m in ("alpha", "beta") if m in plan_data]) == 1


def test_the_absence_check_is_made_under_a_lock_a_second_create_cannot_cross(
        pg_app, monkeypatch):
    """The owner lock, pinned deterministically.

    Creation is the one case the plan-row lock physically cannot cover: with no
    plan there is no row to lock, so the owner row is the only thing standing
    between two callers who have each just observed "there is no plan". Removing
    it left the free-running CASE C green, because two threads and a GIL rarely
    interleave at the one instruction that matters — which would have made the
    owner lock an untested claim.

    So the window is forced open. The first create is paused between its locked
    read and its insert, and the second create is released into it. With the
    owner lock the second caller cannot even reach its own check; without it,
    both observe an empty owner and the user ends up holding two plans.
    """
    from app.extensions import db
    from app.services import plan_replacement

    app, user_id = pg_app
    observed_absence = threading.Event()
    second_returned = threading.Event()
    observed = {}
    paused = []

    real_get_active_plan = plan_replacement.get_active_plan

    def paused_selector(uid):
        plan = real_get_active_plan(uid)
        # Only the FIRST caller pauses. The second must be blocked before it
        # ever gets here — that is the property under test.
        if not paused:
            paused.append(True)
            observed_absence.set()
            observed["second_escaped"] = second_returned.wait(
                LOCK_PROBE_SECONDS)
        return plan

    monkeypatch.setattr(plan_replacement, "get_active_plan", paused_selector)

    def first():
        with app.app_context():
            try:
                observed["first"] = ("ok", _replace(user_id, None, "alpha")())
            except plan_replacement.PlanReplacementConflict as error:
                observed["first"] = ("conflict", error.reason)
            except Exception as error:  # pragma: no cover - surfaced by asserts
                observed["first"] = ("unexpected", type(error).__name__)
            finally:
                observed_absence.set()
                db.session.remove()

    def second():
        with app.app_context():
            observed_absence.wait(timeout=10)
            try:
                observed["second"] = ("ok", _replace(user_id, None, "beta")())
            except plan_replacement.PlanReplacementConflict as error:
                observed["second"] = ("conflict", error.reason)
            except Exception as error:  # pragma: no cover - surfaced by asserts
                observed["second"] = ("unexpected", type(error).__name__)
            finally:
                second_returned.set()
                db.session.remove()

    threads = [threading.Thread(target=first, daemon=True),
               threading.Thread(target=second, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), observed

    # THE assertion: the second create could not run while the first held its
    # critical section open.
    assert observed["second_escaped"] is False, observed
    assert observed["first"][0] == "ok", observed
    assert observed["second"] == ("conflict", "expected_absent_but_plan_exists"), \
        observed

    state = _state(app, user_id)
    assert state["count"] == 1, observed
    _lineage, version, plan_data = state["active"]
    assert version == 0
    assert "alpha" in plan_data and "beta" not in plan_data


# ── CASE D / CASE E — stale expectations refuse without destroying ───────────

def test_a_stale_lineage_refuses_against_real_postgres(seeded):
    from app.services.plan_replacement import (
        PlanExpectation, PlanReplacementConflict, replace_training_plan,
    )

    app, user_id, identity = seeded
    before = _state(app, user_id)
    assert before["active"][:2] == identity, before
    with app.app_context():
        with pytest.raises(PlanReplacementConflict) as raised:
            replace_training_plan(
                user_id,
                PlanExpectation(absent=False, lineage_id="a-retired-lineage",
                                mutation_version=identity[1]),
                plan_data=_proposal("stale"), score=9)
        assert raised.value.reason == "lineage_mismatch"

    state = _state(app, user_id)
    assert state["count"] == 1
    assert state["active"] == (identity[0], identity[1], PROGRAM)


def test_a_stale_version_refuses_against_real_postgres(seeded):
    from app.services.plan_replacement import (
        PlanExpectation, PlanReplacementConflict, replace_training_plan,
    )

    app, user_id, identity = seeded
    before = _state(app, user_id)
    assert before["active"][:2] == identity, before
    with app.app_context():
        with pytest.raises(PlanReplacementConflict) as raised:
            replace_training_plan(
                user_id,
                PlanExpectation(absent=False, lineage_id=identity[0],
                                mutation_version=identity[1] + 1),
                plan_data=_proposal("stale"), score=9)
        assert raised.value.reason == "version_mismatch"

    state = _state(app, user_id)
    assert state["count"] == 1
    assert state["active"] == (identity[0], identity[1], PROGRAM)


# ── Atomicity under a real transaction ───────────────────────────────────────

def test_a_failure_after_the_delete_leaves_the_plan_intact(seeded):
    """Never zero plans.

    The delete and the insert share one PostgreSQL transaction, so a failure
    between them and the commit must roll back to exactly the plan the owner
    had — the state a user would otherwise be left in is "no training plan at
    all", produced by a save they asked for.
    """
    from app.extensions import db
    from app.services.plan_replacement import (
        PlanExpectation, replace_training_plan,
    )

    app, user_id, identity = seeded
    with app.app_context():
        original_commit = db.session.commit
        try:
            db.session.commit = _explode
            with pytest.raises(RuntimeError):
                replace_training_plan(
                    user_id,
                    PlanExpectation(absent=False, lineage_id=identity[0],
                                    mutation_version=identity[1]),
                    plan_data=_proposal("doomed"), score=9)
        finally:
            db.session.commit = original_commit
        db.session.remove()

    state = _state(app, user_id)
    assert state["count"] == 1
    assert state["active"] == (identity[0], identity[1], PROGRAM)


def _explode():
    raise RuntimeError("database went away mid-replacement")
