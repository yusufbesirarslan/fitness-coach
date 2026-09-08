"""The server-side precondition on destructive training-plan replacement.

``POST /training-plan/save`` deletes the user's plan and installs a proposal.
Before this boundary existed it did so unconditionally, so a proposal generated
against plan N could delete a plan that had already moved to N+1 — a real lost
update, not a UX freshness annoyance. A client-side re-read narrows the window;
only the server can close it.

These tests cover the contract and the single-process semantics. The genuinely
concurrent proofs (two replacements from one version, a Coach mutation racing a
replacement, two creates) need real row locks and live in
``tests/test_training_plan_replacement_pg.py``, which CI runs against
PostgreSQL — passing here would prove nothing about them, because SQLite
serializes writers at the file level.
"""
import json

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import TrainingPlan
from app.services.plan_replacement import (
    CODE_PLAN_CHANGED,
    CODE_PRECONDITION_INVALID,
    PlanExpectation,
    PlanPreconditionInvalid,
    PlanReplacementConflict,
    parse_expectation,
    replace_training_plan,
)
from tests.test_training_routes import (  # noqa: F401 - fixture import
    _seven_day_program,
    plan_save_token,
)


PLAN_A = json.dumps({"program": [{"gun": "Pazartesi"}]}, ensure_ascii=False)
PLAN_B = json.dumps({"program": [{"gun": "Salı"}]}, ensure_ascii=False)


# ── helpers ──────────────────────────────────────────────────────────────────

def _seed(user_id, plan_data=PLAN_A, score=7.0):
    plan = TrainingPlan(user_id=user_id, plan_data=plan_data, score=score)
    db.session.add(plan)
    db.session.commit()
    return plan


def _rows(user_id):
    db.session.expire_all()
    return TrainingPlan.query.filter_by(user_id=user_id).all()


def _identity(plan):
    return {"lineage_id": plan.lineage_id,
            "mutation_version": plan.mutation_version}


class delete_spy:
    """Record the DELETEs the database really runs against ``training_plan``.

    Watching the statement rather than a patched query object is what makes
    "nothing was destroyed" evidence instead of an assertion about code shape:
    no refactor of the replacement authority can quietly stop being observed.
    """

    def __enter__(self):
        self.statements = []
        self._engine = db.engine
        event.listen(self._engine, "before_cursor_execute", self._record)
        return self.statements

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._record)
        return False

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        text = statement.lstrip().upper()
        if text.startswith("DELETE") and "TRAINING_PLAN" in text:
            self.statements.append(statement)


def _save(client, token, program=None, expected="__missing__", score=7.0):
    body = {
        "plan": _seven_day_program() if program is None else program,
        "score": score,
        "exercise_context_token": token,
    }
    if expected != "__missing__":
        body["expected_plan"] = expected
    return client.post("/training-plan/save", json=body)


# ── the precondition is READ fail-closed ─────────────────────────────────────

class TestParseExpectation:

    def test_an_omitted_field_is_refused_rather_than_defaulted(self):
        """The whole defect in one case.

        Treating a missing field as "no preference" restores the unconditional
        destructive save for every caller that forgets it — which is precisely
        how the endpoint behaved before.
        """
        with pytest.raises(PlanPreconditionInvalid) as raised:
            parse_expectation({"plan": [], "score": 1})
        assert raised.value.reason == "missing"

    def test_explicit_null_is_the_assertion_that_no_plan_exists(self):
        assert parse_expectation({"expected_plan": None}) == PlanExpectation(
            absent=True)

    def test_a_wellformed_pair_is_accepted(self):
        parsed = parse_expectation({
            "expected_plan": {"lineage_id": "abc", "mutation_version": 4}})
        assert parsed == PlanExpectation(
            absent=False, lineage_id="abc", mutation_version=4)

    @pytest.mark.parametrize("raw,reason", [
        ("abc", "not_an_object"),
        (7, "not_an_object"),
        ([], "not_an_object"),
        ({"lineage_id": "abc"}, "unexpected_fields"),
        ({"mutation_version": 1}, "unexpected_fields"),
        ({"lineage_id": "abc", "mutation_version": 1, "x": 1},
         "unexpected_fields"),
        ({"lineage_id": "", "mutation_version": 1}, "lineage_invalid"),
        ({"lineage_id": None, "mutation_version": 1}, "lineage_invalid"),
        ({"lineage_id": 5, "mutation_version": 1}, "lineage_invalid"),
        ({"lineage_id": "x" * 65, "mutation_version": 1}, "lineage_invalid"),
        ({"lineage_id": "abc", "mutation_version": -1}, "version_invalid"),
        ({"lineage_id": "abc", "mutation_version": "1"}, "version_invalid"),
        ({"lineage_id": "abc", "mutation_version": 1.0}, "version_invalid"),
        ({"lineage_id": "abc", "mutation_version": None}, "version_invalid"),
    ])
    def test_malformed_expectations_are_refused(self, raw, reason):
        with pytest.raises(PlanPreconditionInvalid) as raised:
            parse_expectation({"expected_plan": raw})
        assert raised.value.reason == reason

    def test_a_boolean_version_is_not_quietly_an_integer(self):
        """``bool`` subclasses ``int``: ``True`` would otherwise mean version 1
        and could match a real plan sitting at version 1."""
        with pytest.raises(PlanPreconditionInvalid) as raised:
            parse_expectation({
                "expected_plan": {"lineage_id": "abc", "mutation_version": True}})
        assert raised.value.reason == "version_invalid"

    def test_a_nonobject_body_is_refused(self):
        with pytest.raises(PlanPreconditionInvalid) as raised:
            parse_expectation(["expected_plan"])
        assert raised.value.reason == "payload_invalid"


# ── the replacement authority ────────────────────────────────────────────────

class TestReplacementAuthority:

    def test_a_matching_expectation_replaces_the_plan(self, app, make_user):
        user = make_user("rep_match")
        seeded = _seed(user.id)
        before = seeded.lineage_id

        result = replace_training_plan(
            user.id, PlanExpectation(absent=False, lineage_id=before,
                                     mutation_version=0),
            plan_data=PLAN_B, score=9.0)

        rows = _rows(user.id)
        assert len(rows) == 1
        assert rows[0].plan_data == PLAN_B
        # Replacement semantics are unchanged: a NEW lineage at version 0.
        assert rows[0].lineage_id != before
        assert rows[0].mutation_version == 0
        assert result.lineage_id == rows[0].lineage_id
        assert result.mutation_version == 0

    def test_a_stale_version_refuses_and_destroys_nothing(self, app, make_user):
        user = make_user("rep_version")
        seeded = _seed(user.id)
        seeded.mutation_version = 3
        db.session.commit()

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(
                    user.id,
                    PlanExpectation(absent=False, lineage_id=seeded.lineage_id,
                                    mutation_version=2),
                    plan_data=PLAN_B, score=9.0)

        assert raised.value.reason == "version_mismatch"
        assert deletes == []
        rows = _rows(user.id)
        assert len(rows) == 1 and rows[0].plan_data == PLAN_A

    def test_a_stale_lineage_refuses_and_destroys_nothing(self, app, make_user):
        user = make_user("rep_lineage")
        _seed(user.id)

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(
                    user.id,
                    PlanExpectation(absent=False, lineage_id="a-different-plan",
                                    mutation_version=0),
                    plan_data=PLAN_B, score=9.0)

        assert raised.value.reason == "lineage_mismatch"
        assert deletes == []
        assert _rows(user.id)[0].plan_data == PLAN_A

    def test_the_pair_is_the_identity_not_the_version_alone(
            self, app, make_user):
        """Every replacement starts a new lineage at version 0, so a version
        that "matches" across lineages is the common case, not an exotic one."""
        user = make_user("rep_pair")
        _seed(user.id)

        with pytest.raises(PlanReplacementConflict) as raised:
            replace_training_plan(
                user.id,
                PlanExpectation(absent=False, lineage_id="some-other-lineage",
                                mutation_version=0),
                plan_data=PLAN_B, score=9.0)
        assert raised.value.reason == "lineage_mismatch"

    def test_expecting_a_plan_that_no_longer_exists_refuses(
            self, app, make_user):
        user = make_user("rep_gone")
        with pytest.raises(PlanReplacementConflict) as raised:
            replace_training_plan(
                user.id,
                PlanExpectation(absent=False, lineage_id="gone",
                                mutation_version=0),
                plan_data=PLAN_B, score=9.0)
        assert raised.value.reason == "expected_present_but_absent"

    def test_creation_succeeds_and_issues_no_destructive_statement(
            self, app, make_user):
        """A create must not DELETE.

        The unconditional version ran ``DELETE ... WHERE user_id = :me`` even
        when it believed there was nothing to delete — so a plan another writer
        committed in the meantime was destroyed by a request that never
        intended to replace anything.
        """
        user = make_user("rep_create")

        with delete_spy() as deletes:
            result = replace_training_plan(
                user.id, PlanExpectation(absent=True),
                plan_data=PLAN_A, score=5.0)

        assert deletes == []
        rows = _rows(user.id)
        assert len(rows) == 1 and rows[0].plan_data == PLAN_A
        assert result.mutation_version == 0
        assert result.lineage_id == rows[0].lineage_id

    def test_expecting_absence_when_a_plan_exists_refuses(
            self, app, make_user):
        user = make_user("rep_absent")
        _seed(user.id)

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(
                    user.id, PlanExpectation(absent=True),
                    plan_data=PLAN_B, score=9.0)

        assert raised.value.reason == "expected_absent_but_plan_exists"
        assert deletes == []
        assert _rows(user.id)[0].plan_data == PLAN_A

    def test_a_plan_outside_the_locked_set_refuses(
            self, app, make_user, monkeypatch):
        """The guard for a writer that does not take this lock.

        Forced through the canonical selector rather than through a thread,
        because the point is the branch: if the active plan is not one of the
        rows this transaction locked, it is not this transaction's to delete.
        """
        from app.services import plan_replacement

        user = make_user("rep_appeared")
        seeded = _seed(user.id)
        intruder = TrainingPlan(user_id=user.id, plan_data=PLAN_B, score=1.0)
        intruder.id = seeded.id + 10_000

        monkeypatch.setattr(
            plan_replacement, "get_active_plan", lambda _uid: intruder)

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(
                    user.id,
                    PlanExpectation(absent=False, lineage_id=seeded.lineage_id,
                                    mutation_version=0),
                    plan_data=PLAN_B, score=9.0)

        assert raised.value.reason == "plan_appeared"
        assert deletes == []
        assert _rows(user.id)[0].plan_data == PLAN_A

    def test_a_lineage_never_addresses_another_users_plan(
            self, app, make_user):
        """Freshness is not authorization.

        Naming someone else's lineage cannot select their row: every statement
        is owner-scoped, so the expectation simply fails to match this caller's
        own state — and the other user's plan is untouched.
        """
        victim = make_user("rep_victim")
        attacker = make_user("rep_attacker")
        target = _seed(victim.id)

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(
                    attacker.id,
                    PlanExpectation(absent=False, lineage_id=target.lineage_id,
                                    mutation_version=0),
                    plan_data=PLAN_B, score=9.0)

        assert raised.value.reason == "expected_present_but_absent"
        assert deletes == []
        assert _rows(victim.id)[0].plan_data == PLAN_A
        assert _rows(attacker.id) == []

    def test_a_commit_failure_leaves_the_current_plan_intact(
            self, app, make_user, monkeypatch):
        """Atomicity, proved from the inside.

        The delete and the insert are in one transaction, so a failure after
        both statements must leave the owner with exactly the plan they had —
        never zero plans, never a half-written replacement.
        """
        user = make_user("rep_rollback")
        seeded = _seed(user.id)
        lineage = seeded.lineage_id

        def _boom():
            raise RuntimeError("database went away mid-replacement")

        monkeypatch.setattr(db.session, "commit", _boom)

        with pytest.raises(RuntimeError):
            replace_training_plan(
                user.id,
                PlanExpectation(absent=False, lineage_id=lineage,
                                mutation_version=0),
                plan_data=PLAN_B, score=9.0)

        monkeypatch.undo()
        rows = _rows(user.id)
        assert len(rows) == 1
        assert rows[0].lineage_id == lineage
        assert rows[0].plan_data == PLAN_A


# ── the coach mutation boundary ──────────────────────────────────────────────

class TestCoachMutationInteraction:

    def test_a_coach_mutation_makes_an_earlier_expectation_stale(
            self, app, make_user):
        """The exact lost update this prerequisite exists to prevent.

        The client read (L, 0), the Coach moved the plan to (L, 1), and the
        client's stale proposal must now be refused rather than delete the
        Coach's work.
        """
        from app.services.plan_mutation import (
            MutationContext, ReplaceExerciseCommand, apply_plan_mutation,
        )

        user = make_user("rep_coach")
        program = {
            "program": [
                {"gun": "Pazartesi", "tip": "antrenman", "odak": "Itis",
                 "egzersizler": [
                     {"isim": "Bench Press", "set": 3, "tekrar": "8-12"}]},
            ],
            "haftalik_ozet": {"yogunluk_skoru": 7},
        }
        seeded = _seed(user.id, json.dumps(program, ensure_ascii=False))
        stale = PlanExpectation(absent=False, lineage_id=seeded.lineage_id,
                                mutation_version=0)

        apply_plan_mutation(
            user.id,
            ReplaceExerciseCommand(day="Pazartesi", exercise="Bench Press",
                                   replacement="Machine Press"),
            MutationContext(idempotency_key="rep-coach-0001"))

        with delete_spy() as deletes:
            with pytest.raises(PlanReplacementConflict) as raised:
                replace_training_plan(user.id, stale, plan_data=PLAN_B,
                                      score=9.0)

        assert raised.value.reason == "version_mismatch"
        assert deletes == []
        current = _rows(user.id)[0]
        assert current.mutation_version == 1
        assert "Machine Press" in current.plan_data

    def test_a_replacement_that_wins_leaves_the_coach_a_fresh_lineage(
            self, app, make_user):
        """A legitimate replacement still ends the old lineage.

        The Coach's journal is not rewritten and its undo simply becomes
        unavailable for the retired lineage — the Sprint 1 PR2 semantics, kept
        exactly as they were.
        """
        from app.services.plan_mutation import (
            MutationContext, UndoUnavailable, undo_last_change,
        )

        user = make_user("rep_coach_win")
        seeded = _seed(user.id)

        replace_training_plan(
            user.id,
            PlanExpectation(absent=False, lineage_id=seeded.lineage_id,
                            mutation_version=0),
            plan_data=PLAN_B, score=9.0)

        with pytest.raises(UndoUnavailable):
            undo_last_change(user.id, MutationContext(
                idempotency_key="rep-coach-win-0001"))


# ── the HTTP contract ────────────────────────────────────────────────────────

class TestSaveRouteContract:

    def test_a_save_without_a_precondition_is_refused(
            self, client, auth_user, plan_save_token):
        _seed(auth_user.id)

        with delete_spy() as deletes:
            response = _save(client, plan_save_token(auth_user.id))

        assert response.status_code == 400
        body = response.get_json()
        assert body["code"] == CODE_PRECONDITION_INVALID
        assert body["retryable"] is False
        assert deletes == []
        assert _rows(auth_user.id)[0].plan_data == PLAN_A

    @pytest.mark.parametrize("expected", [
        "not-an-object",
        {"lineage_id": "abc"},
        {"lineage_id": "abc", "mutation_version": -3},
        {"lineage_id": "abc", "mutation_version": "0"},
    ])
    def test_a_malformed_precondition_is_a_bounded_400(
            self, client, auth_user, plan_save_token, expected):
        _seed(auth_user.id)

        response = _save(client, plan_save_token(auth_user.id),
                         expected=expected)

        assert response.status_code == 400
        assert response.get_json()["code"] == CODE_PRECONDITION_INVALID
        assert _rows(auth_user.id)[0].plan_data == PLAN_A

    def test_a_matching_precondition_saves_and_returns_the_new_identity(
            self, client, auth_user, plan_save_token):
        seeded = _seed(auth_user.id)

        response = _save(client, plan_save_token(auth_user.id),
                         expected=_identity(seeded))

        assert response.status_code == 200
        body = response.get_json()
        rows = _rows(auth_user.id)
        assert len(rows) == 1
        assert body["plan"] == {"lineage_id": rows[0].lineage_id,
                                "mutation_version": 0}
        assert rows[0].lineage_id != seeded.lineage_id

    def test_a_first_save_declares_absence_and_succeeds(
            self, client, auth_user, plan_save_token):
        assert _rows(auth_user.id) == []

        response = _save(client, plan_save_token(auth_user.id), expected=None)

        assert response.status_code == 200
        assert len(_rows(auth_user.id)) == 1

    def test_a_stale_precondition_is_a_typed_409(
            self, client, auth_user, plan_save_token):
        seeded = _seed(auth_user.id)
        stale = {"lineage_id": seeded.lineage_id, "mutation_version": 4}

        with delete_spy() as deletes:
            response = _save(client, plan_save_token(auth_user.id),
                             expected=stale)

        assert response.status_code == 409
        body = response.get_json()
        assert body["code"] == CODE_PLAN_CHANGED
        assert body["retryable"] is False
        assert deletes == []
        assert _rows(auth_user.id)[0].plan_data == PLAN_A

    def test_declaring_absence_when_a_plan_exists_is_a_409(
            self, client, auth_user, plan_save_token):
        """A no-plan page must not overwrite a concurrently created plan."""
        _seed(auth_user.id)

        with delete_spy() as deletes:
            response = _save(client, plan_save_token(auth_user.id),
                             expected=None)

        assert response.status_code == 409
        assert response.get_json()["code"] == CODE_PLAN_CHANGED
        assert deletes == []
        assert _rows(auth_user.id)[0].plan_data == PLAN_A

    def test_a_conflict_body_leaks_nothing_about_the_current_plan(
            self, client, auth_user, plan_save_token):
        """A refusal explains the situation, never the state.

        The response must not become a read channel for the plan the caller
        failed to name — no lineage, no version, no row id, no plan content.
        """
        seeded = _seed(auth_user.id)

        response = _save(client, plan_save_token(auth_user.id),
                         expected={"lineage_id": seeded.lineage_id,
                                   "mutation_version": 9})

        raw = response.get_data(as_text=True)
        assert set(response.get_json()) == {"error", "code", "retryable"}
        assert seeded.lineage_id not in raw
        assert "Pazartesi" not in raw
        assert str(seeded.id) not in response.get_json()["code"]

    def test_a_stale_precondition_never_reaches_validation(
            self, client, auth_user, plan_save_token, monkeypatch):
        """Order matters both ways.

        A malformed precondition is refused BEFORE the signed context and the
        catalog are consulted, so a doomed request cannot spend that work.
        """
        from app.blueprints import training as training_bp

        _seed(auth_user.id)
        called = []
        monkeypatch.setattr(
            training_bp, "resolve_save_exercise_context",
            lambda *a, **k: called.append(1))

        response = _save(client, plan_save_token(auth_user.id),
                         expected={"lineage_id": 5, "mutation_version": 0})

        assert response.status_code == 400
        assert called == []


# ── the identity a client needs in order to obey the contract ────────────────

class TestCanonicalIdentityIsReadable:

    def test_the_active_plan_read_publishes_the_freshness_pair(
            self, client, auth_user):
        seeded = _seed(auth_user.id)

        body = client.get("/training-plan/active").get_json()

        assert body["exists"] is True
        assert body["lineage_id"] == seeded.lineage_id
        assert body["mutation_version"] == 0

    def test_the_no_plan_shape_is_unchanged(self, client, auth_user):
        assert client.get("/training-plan/active").get_json() == {
            "exists": False}

    def test_the_bootstrap_snapshot_publishes_the_same_pair(
            self, client, auth_user):
        # Read the identity out BEFORE the request: /training/bootstrap opens a
        # coherent read snapshot, which calls db.session.remove() and detaches
        # every instance this test is holding.
        lineage = _seed(
            auth_user.id,
            json.dumps({"program": _seven_day_program()},
                       ensure_ascii=False)).lineage_id

        body = client.get("/training/bootstrap").get_json()

        assert body["plan"]["exists"] is True
        assert body["plan"]["lineage_id"] == lineage
        assert body["plan"]["mutation_version"] == 0

    def test_a_round_trip_uses_only_what_the_server_published(
            self, client, auth_user, plan_save_token):
        """The contract closes: read the identity, send it back, save."""
        _seed(auth_user.id,
              json.dumps({"program": _seven_day_program()}, ensure_ascii=False))
        published = client.get("/training-plan/active").get_json()

        response = _save(
            client, plan_save_token(auth_user.id),
            expected={"lineage_id": published["lineage_id"],
                      "mutation_version": published["mutation_version"]})

        assert response.status_code == 200
        # And the identity the save hands back is immediately usable again.
        again = _save(client, plan_save_token(auth_user.id),
                      expected=response.get_json()["plan"])
        assert again.status_code == 200
