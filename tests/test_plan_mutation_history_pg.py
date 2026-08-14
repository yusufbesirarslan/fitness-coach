"""Opt-in PostgreSQL races for plan versioning, replay and undo.

SQLite cannot arbitrate these: it serializes writers at the file level, so a test
that passes there proves nothing about two workers hitting one row on PostgreSQL.
These run against a disposable database and are collected in CI's
``mobile-pg-concurrency`` job.

Every race is coordinated with ``threading.Barrier`` — the contenders arrive
together deterministically. No ``sleep()`` anywhere: a timing-based race test
either passes by luck or turns into a flake, and both are worse than no test.
"""
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


PROGRAM = (
    '{"program": ['
    '{"gun": "Pazartesi", "tip": "antrenman", "odak": "Itis", '
    '"egzersizler": [{"isim": "Bench Press", "set": 3, "tekrar": "8-12"}, '
    '{"isim": "Shoulder Press", "set": 4, "tekrar": "10-12"}]}, '
    '{"gun": "Carsamba", "tip": "antrenman", "odak": "Cekis", '
    '"egzersizler": [{"isim": "Barbell Row", "set": 4, "tekrar": "6-10"}]}'
    '], "haftalik_ozet": {"yogunluk_skoru": 7}}'
)


@pytest.fixture
def pg_plan_app():
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
    from app.models import TrainingPlan, User

    app = Flask("plan-mutation-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-plan-mutation-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg-plan", email="pg-plan@example.invalid",
                    cognito_sub="pg-plan-sub")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        db.session.add(TrainingPlan(user_id=user_id, plan_data=PROGRAM, score=8))
        db.session.commit()
    try:
        yield app, user_id
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _state(app, user_id):
    from app.models import PlanMutationRecord
    from app.services.today_facts import get_active_plan

    with app.app_context():
        plan = get_active_plan(user_id)
        records = (PlanMutationRecord.query.filter_by(user_id=user_id)
                   .order_by(PlanMutationRecord.id).all())
        return (plan.plan_data, plan.mutation_version,
                [(r.operation_kind, r.outcome, r.idempotency_key) for r in records])


def _run(app, calls):
    """Release ``len(calls)`` threads at one barrier and collect their outcomes."""
    from app.extensions import db
    from app.services.plan_mutation import (
        IdempotencyConflict, UndoConflict, UndoUnavailable,
    )

    barrier = threading.Barrier(len(calls))
    outcomes = {}

    def contender(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                result = calls[index]()
                outcomes[index] = ("ok", result.outcome, result.plan_version,
                                   result.mutation_id, result.replayed)
            except IdempotencyConflict:
                outcomes[index] = ("idempotency_conflict",)
            except UndoConflict:
                outcomes[index] = ("undo_conflict",)
            except UndoUnavailable:
                outcomes[index] = ("undo_unavailable",)
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


def _mutation(user_id, command, key):
    from app.services.plan_mutation import MutationContext, apply_plan_mutation
    return lambda: apply_plan_mutation(
        user_id, command, MutationContext(idempotency_key=key))


def _undo(user_id, key):
    from app.services.plan_mutation import MutationContext, undo_last_change
    return lambda: undo_last_change(user_id, MutationContext(idempotency_key=key))


def _add(exercise):
    from app.services.plan_mutation import AddExerciseCommand
    return AddExerciseCommand(day="Carsamba", exercise=exercise, sets=3,
                              reps="30 sn")


def _seed_one_mutation(app, user_id):
    from app.services.plan_mutation import ReplaceExerciseCommand
    with app.app_context():
        _mutation(user_id, ReplaceExerciseCommand(
            day="Pazartesi", exercise="Bench Press",
            replacement="Machine Press"), "pg-seed-0001")()


# ── Race A: one key, one command ─────────────────────────────────────────────

def test_concurrent_same_key_same_command_applies_once(pg_plan_app):
    """The duplicate-``add`` race, run for real.

    Both workers pass their pre-flight check before either commits, so both
    reach the INSERT. Only the unique constraint can decide this — and the loser
    must come back with the winner's result, not an error and not a second
    exercise.
    """
    app, user_id = pg_plan_app
    command = _add("Plank")
    outcomes = _run(app, [_mutation(user_id, command, "pg-race-a-0001")] * 2)

    assert [outcomes[i][0] for i in range(2)] == ["ok", "ok"], outcomes
    assert {outcomes[i][3] for i in range(2)} == {outcomes[0][3]}
    assert {outcomes[i][2] for i in range(2)} == {1}
    assert sorted(outcomes[i][4] for i in range(2)) == [False, True]

    plan_data, version, records = _state(app, user_id)
    assert version == 1
    assert plan_data.count('"Plank"') == 1
    assert records == [("mutation", "applied", "pg-race-a-0001")]


# ── Race B: one key, two different commands ──────────────────────────────────

def test_concurrent_same_key_different_commands_yields_one_winner(pg_plan_app):
    app, user_id = pg_plan_app
    outcomes = _run(app, [
        _mutation(user_id, _add("Plank"), "pg-race-b-0001"),
        _mutation(user_id, _add("Face Pull"), "pg-race-b-0001"),
    ])

    kinds = sorted(outcome[0] for outcome in outcomes.values())
    assert kinds == ["idempotency_conflict", "ok"], outcomes

    plan_data, version, records = _state(app, user_id)
    assert version == 1
    assert len(records) == 1
    applied = plan_data.count('"Plank"') + plan_data.count('"Face Pull"')
    assert applied == 1


# ── Race C: two keys, two commands ───────────────────────────────────────────

def test_concurrent_distinct_keys_never_lose_an_update(pg_plan_app):
    """Both changes must survive and the version must count both.

    The wrong implementation here is subtle and silent: take the row lock, but
    compute the new state from a plan instance the session already had in
    memory. The lock is real, the write happens, and the second mutation
    overwrites the first while the version still says 1.
    """
    app, user_id = pg_plan_app
    outcomes = _run(app, [
        _mutation(user_id, _add("Plank"), "pg-race-c-0001"),
        _mutation(user_id, _add("Face Pull"), "pg-race-c-0002"),
    ])

    assert [outcomes[i][0] for i in range(2)] == ["ok", "ok"], outcomes
    assert sorted(outcomes[i][2] for i in range(2)) == [1, 2]

    plan_data, version, records = _state(app, user_id)
    assert version == 2
    assert '"Plank"' in plan_data and '"Face Pull"' in plan_data
    assert [r[0] for r in records] == ["mutation", "mutation"]


# ── Race D: one undo key, retried concurrently ───────────────────────────────

def test_concurrent_same_key_undo_reverses_exactly_once(pg_plan_app):
    app, user_id = pg_plan_app
    _seed_one_mutation(app, user_id)

    outcomes = _run(app, [_undo(user_id, "pg-race-d-0001")] * 2)

    assert [outcomes[i][0] for i in range(2)] == ["ok", "ok"], outcomes
    assert {outcomes[i][3] for i in range(2)} == {outcomes[0][3]}
    assert {outcomes[i][2] for i in range(2)} == {2}

    plan_data, version, records = _state(app, user_id)
    assert plan_data == PROGRAM
    assert version == 2
    assert [r[0] for r in records] == ["mutation", "undo"]


# ── Race E: two undo keys, one reversible mutation ───────────────────────────

def test_concurrent_distinct_key_undos_reverse_only_one_change(pg_plan_app):
    """Two genuinely different undo requests race for one target.

    Exactly one may win. The other must fail closed rather than walk a second
    step back — the user asked to undo one change, twice by accident, not two
    changes.
    """
    app, user_id = pg_plan_app
    _seed_one_mutation(app, user_id)

    outcomes = _run(app, [
        _undo(user_id, "pg-race-e-0001"),
        _undo(user_id, "pg-race-e-0002"),
    ])

    kinds = sorted(outcome[0] for outcome in outcomes.values())
    assert kinds in (["ok", "undo_conflict"], ["ok", "undo_unavailable"]), outcomes

    plan_data, version, records = _state(app, user_id)
    assert plan_data == PROGRAM
    assert version == 2
    assert [r[0] for r in records] == ["mutation", "undo"]
