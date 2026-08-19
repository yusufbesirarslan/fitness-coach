"""Behaviour PR3 inherits and must not break (brief §66).

Written BEFORE the plan-mutation tools exist, against the merged PR1/PR2 + AI
Coach code, so a later refactor cannot quietly move one of these load-bearing
facts. Three groups:

* the tool registry / dispatch contract every existing Coach tool relies on,
* the turn identity, deadline and loop budget PR3's operation key and mutation
  budget are built on top of,
* the transaction state the mutation service leaves behind — the PR2 "Minor"
  that becomes load-bearing the moment a provider call can follow a tool
  (brief §10).

The transaction assertions are the ones that matter most. They are written as
positive invariants ("no transaction remains") rather than as a description of
today's bug, so the same file proves the fix and then keeps proving it.
"""
import json

import pytest

from app.services import ai_coach
from app.services.plan_mutation import (
    AddExerciseCommand,
    IdempotencyConflict,
    MutationContext,
    ReplaceExerciseCommand,
    UndoUnavailable,
    apply_plan_mutation,
    undo_last_change,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _program():
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "İtiş",
             "egzersizler": [
                 {"isim": "Bench Press", "set": 3, "tekrar": "8-12",
                  "dinlenme": "90 sn"},
                 {"isim": "Shoulder Press", "set": 4, "tekrar": "10-12"}]},
            {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "egzersizler": [
                {"isim": "Barbell Row", "set": 4, "tekrar": "6-10"}]},
            {"gun": "Perşembe", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "egzersizler": [
                {"isim": "Squat", "set": 5, "tekrar": "5"}]},
            {"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "egzersizler": []},
        ],
        "haftalik_ozet": {"toplam_antrenman_gun": 3},
    }


@pytest.fixture
def planned_user(app, make_user):
    """A user holding one canonical active plan."""
    from app.extensions import db
    from app.models import TrainingPlan

    user = make_user("planner")
    db.session.add(TrainingPlan(
        user_id=user.id, score=8,
        plan_data=json.dumps(_program(), ensure_ascii=False)))
    db.session.commit()
    return user


def open_transaction():
    """True when the ORM session still holds a transaction.

    ``db.session`` is a ``scoped_session`` and does NOT proxy
    ``in_transaction``; calling it returns the real ``Session``. Getting this
    wrong yields ``AttributeError``, not ``False``, so the helper is shared
    rather than repeated.
    """
    from app.extensions import db
    return db.session().in_transaction()


def _ctx(key):
    return MutationContext(idempotency_key=key)


def _replace():
    return ReplaceExerciseCommand(
        day="Pazartesi", exercise="Bench Press", replacement="Machine Press")


def _add(name="Cable Fly"):
    return AddExerciseCommand(
        day="Pazartesi", exercise=name, sets=3, reps="12")


def _day_names(user_id, day="Pazartesi"):
    """The persisted exercise names of one day — state, not a mock call log."""
    from app.services.today_facts import get_active_plan

    document = json.loads(get_active_plan(user_id).plan_data)
    entry = next(d for d in document["program"] if d["gun"] == day)
    return [exercise["isim"] for exercise in entry["egzersizler"]]


# ── The tool registry and dispatch contract ──────────────────────────────────

def test_one_registry_feeds_both_provider_formats():
    """Both providers are derived from a single list, in a single order.

    PR3 adds tools to that list; if the two formats ever stopped being derived
    from one source, a mutation tool could exist on one provider and not the
    other, which is exactly the drift brief §41 forbids.
    """
    names = [t["name"] for t in ai_coach._COACH_TOOL_DEFS]

    assert [t["function"]["name"] for t in ai_coach.COACH_TOOLS] == names
    assert [t["name"] for t in ai_coach.COACH_TOOLS_ANTHROPIC] == names
    for base, openai_tool, anthropic_tool in zip(
            ai_coach._COACH_TOOL_DEFS, ai_coach.COACH_TOOLS,
            ai_coach.COACH_TOOLS_ANTHROPIC):
        assert openai_tool["function"]["parameters"] is base["parameters"]
        assert anthropic_tool["input_schema"] is base["parameters"]


def test_every_existing_tool_has_a_dispatch_branch(app, make_user):
    """Every registered name resolves to a handler, and an unknown name does
    not. PR3 extends this router; the existing seven must keep routing."""
    user = make_user("router")
    with app.test_request_context():
        for definition in ai_coach._COACH_TOOL_DEFS:
            payload = json.loads(ai_coach._dispatch_coach_tool(
                user.id, definition["name"], "{}"))
            assert payload.get("message", "") != (
                f"Bilinmeyen araç: {definition['name']}")

        unknown = json.loads(ai_coach._dispatch_coach_tool(
            user.id, "definitely_not_a_tool", "{}"))

    assert unknown["status"] == "error"
    assert "Bilinmeyen araç" in unknown["message"]


def test_dispatch_never_takes_user_id_from_the_model(app, make_user):
    """``user_id`` is a positional server argument, not a tool argument.

    Pinned structurally: no registered schema may declare a ``user_id``
    property, and a model that invents one must not be able to reach the
    handler through it.
    """
    user = make_user("principal")
    for definition in ai_coach._COACH_TOOL_DEFS:
        properties = definition["parameters"].get("properties", {})
        assert "user_id" not in properties

    with app.test_request_context():
        payload = json.loads(ai_coach._dispatch_coach_tool(
            user.id, "query_fitx_metrics",
            json.dumps({"metric_type": "calories", "date_range": "today",
                        "user_id": user.id + 1})))

    assert payload["status"] == "ok"  # the extra field simply never arrives


def test_dispatch_converts_an_unexpected_tool_failure_into_a_result(
        app, make_user, monkeypatch):
    """A tool body that raises must not crash the Coach turn (brief §73)."""
    user = make_user("failing")

    def boom(*_args, **_kwargs):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(ai_coach, "_tool_query_fitx_metrics", boom)
    with app.test_request_context():
        payload = json.loads(ai_coach._dispatch_coach_tool(
            user.id, "query_fitx_metrics",
            json.dumps({"metric_type": "calories", "date_range": "today"})))

    assert payload["status"] == "error"
    assert "storage exploded" not in payload["message"]


# ── Turn identity, deadline, loop budget ─────────────────────────────────────

def test_the_request_id_is_the_server_owned_turn_identity(app):
    """PR3's operation key is derived from this value, so four properties are
    pinned: it is stable within a turn, a client header cannot choose it, two
    turns get different ones, and outside a turn there is none at all."""
    from app.observability import assign_request_id, current_request_id

    with app.test_request_context(headers={"X-Request-Id": "client-chosen"}):
        assign_request_id()
        first = current_request_id()
        assert current_request_id() == first

    with app.test_request_context():
        assign_request_id()
        second = current_request_id()

    assert first != "client-chosen"
    assert len(first) == 16
    assert first != second

    # No turn, no identity — PR3 must fail closed here rather than mint a key
    # that two Coach turns could share. A fresh app context is used because the
    # test fixture's context still carries the ids assigned above.
    with app.app_context():
        assert current_request_id() == "-"


def test_the_tool_loop_budget_is_bounded():
    """The mutation budget PR3 adds has to be stricter than this (brief §34)."""
    assert ai_coach._COACH_TOOL_LOOP_CAP == 5


def test_the_turn_deadline_is_absolute_and_monotonic(monkeypatch):
    """One deadline is established per turn and only counts down. A plan
    mutation must not be able to extend it (brief §42)."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(ai_coach.time, "monotonic", lambda: clock["now"])

    deadline = ai_coach._coach_turn_deadline()
    full = ai_coach._remaining_coach_turn_seconds(deadline)
    clock["now"] += 10.0

    assert ai_coach._remaining_coach_turn_seconds(deadline) == full - 10.0
    clock["now"] += 10_000.0
    assert ai_coach._remaining_coach_turn_seconds(deadline) == 0.0


def test_provider_fallback_is_refused_once_a_tool_has_run(app, make_user,
                                                          monkeypatch):
    """The B-rule: Bedrock→OpenAI only before any tool side effect.

    PR3 leans on this, but must not depend on it alone — hence the duplicate
    delivery tests elsewhere. Pinning it here means a future relaxation shows
    up as a failure in the file that explains why it mattered.
    """
    user = make_user("brule")
    calls = {"n": 0}

    class _Block:
        type = "tool_use"
        id = "tu_1"
        name = "query_fitx_metrics"
        input = {"metric_type": "calories", "date_range": "today"}

    class _Resp:
        stop_reason = "tool_use"
        content = [_Block()]

    class _Messages:
        def create(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp()
            raise RuntimeError("provider died after the tool ran")

    monkeypatch.setattr(ai_coach, "bedrock_client",
                        type("C", (), {"messages": _Messages()})())
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())

    with app.test_request_context():
        text = ai_coach._run_coach_conversation_bedrock(
            user.id, "kaç kalori aldım?", "", [], "tr")

    # A soft fallback message, NOT a _BedrockFallback escalation to OpenAI.
    assert text == ai_coach._COACH_FALLBACKS["tr"]["tool"]
    assert calls["n"] == 2


# ── Transaction hygiene of the merged mutation service (brief §10) ───────────

def test_applied_mutation_leaves_no_open_transaction(planned_user):
    apply_plan_mutation(planned_user.id, _replace(), _ctx("chr-applied-01"))

    assert not open_transaction()


def test_no_op_mutation_leaves_no_open_transaction(planned_user):
    command = ReplaceExerciseCommand(
        day="Pazartesi", exercise="Bench Press", replacement="Bench Press")
    result = apply_plan_mutation(planned_user.id, command, _ctx("chr-noop-01"))

    assert not result.changed
    assert not open_transaction()


def test_replay_leaves_no_open_transaction(planned_user):
    command, context = _add(), _ctx("chr-replay-01")
    apply_plan_mutation(planned_user.id, command, context)
    result = apply_plan_mutation(planned_user.id, command, context)

    assert result.replayed
    assert not open_transaction()


def test_idempotency_conflict_leaves_no_open_transaction(planned_user):
    context = _ctx("chr-conflict-1")
    apply_plan_mutation(planned_user.id, _add("Cable Fly"), context)

    with pytest.raises(IdempotencyConflict):
        apply_plan_mutation(planned_user.id, _add("Curl"), context)

    assert not open_transaction()


def test_rejected_command_leaves_no_open_transaction(planned_user):
    from app.services.plan_mutation import ExerciseNotFound

    with pytest.raises(ExerciseNotFound):
        apply_plan_mutation(
            planned_user.id,
            ReplaceExerciseCommand(day="Pazartesi", exercise="Nonexistent",
                                   replacement="Machine Press"),
            _ctx("chr-reject-01"))

    assert not open_transaction()


def test_undo_success_leaves_no_open_transaction(planned_user):
    apply_plan_mutation(planned_user.id, _add(), _ctx("chr-undo-seed"))
    undo_last_change(planned_user.id, _ctx("chr-undo-0001"))

    assert not open_transaction()


def test_undo_replay_leaves_no_open_transaction(planned_user):
    apply_plan_mutation(planned_user.id, _add(), _ctx("chr-undo-seed2"))
    context = _ctx("chr-undo-0002")
    undo_last_change(planned_user.id, context)
    result = undo_last_change(planned_user.id, context)

    assert result.replayed
    assert not open_transaction()


def test_undo_unavailable_leaves_no_open_transaction(planned_user):
    with pytest.raises(UndoUnavailable):
        undo_last_change(planned_user.id, _ctx("chr-undo-none1"))

    assert not open_transaction()


# ── The specific PR2 M3 paths: database-arbitrated races ─────────────────────
# Both functions resolve a lost race by rolling back and re-reading the winning
# journal row. That re-read autobegins a NEW transaction, and before PR3 nothing
# closed it. PR2 could call that Minor because no production caller existed; a
# Coach tool makes it a transaction held open across the next provider call.

@pytest.fixture
def blind_replay_lookup(monkeypatch):
    """Make both pre-insert replay lookups miss, so the insert loses the race.

    This is how a genuine concurrent duplicate behaves — both requests read
    before either committed — reproduced deterministically instead of with
    threads, which SQLite cannot arbitrate.
    """
    from app.services.plan_mutation import service as svc

    def _arm():
        calls = {"n": 0}
        real = svc.find_by_key

        def lookup(user_id, key):
            calls["n"] += 1
            return None if calls["n"] <= 2 else real(user_id, key)

        monkeypatch.setattr(svc, "find_by_key", lookup)

    return _arm


def test_database_arbitrated_replay_leaves_no_open_transaction(
        planned_user, blind_replay_lookup):
    command, context = _add(), _ctx("chr-race-0001")
    apply_plan_mutation(planned_user.id, command, context)
    blind_replay_lookup()

    result = apply_plan_mutation(planned_user.id, command, context)

    assert result.replayed
    assert not open_transaction()


def test_database_arbitrated_conflict_leaves_no_open_transaction(
        planned_user, blind_replay_lookup):
    context = _ctx("chr-race-0002")
    apply_plan_mutation(planned_user.id, _add("Cable Fly"), context)
    blind_replay_lookup()

    with pytest.raises(IdempotencyConflict):
        apply_plan_mutation(planned_user.id, _add("Curl"), context)

    assert not open_transaction()


def test_database_arbitrated_undo_replay_leaves_no_open_transaction(
        planned_user, blind_replay_lookup):
    """Two mutations, one undo, then a blind re-delivery of that same undo.

    This is the scenario brief §52 calls load-bearing. With the replay lookup
    blinded, ``latest_reversible`` legitimately points at the EARLIER mutation
    and its byte precondition holds, so nothing above the database can tell
    this from a real second undo — only the unique operation key stops it. The
    assertion is therefore both "one undo happened" and "no transaction is
    still open afterwards".
    """
    apply_plan_mutation(planned_user.id, _add("Cable Fly"), _ctx("chr-r3-m1"))
    apply_plan_mutation(planned_user.id, _add("Curl"), _ctx("chr-r3-m2"))
    context = _ctx("chr-race-0003")
    first = undo_last_change(planned_user.id, context)
    blind_replay_lookup()

    result = undo_last_change(planned_user.id, context)
    # Sampled BEFORE any assertion that queries: reading the plan back would
    # autobegin a transaction of the test's own making.
    residual = open_transaction()

    assert not residual
    assert result.replayed
    assert result.plan_version == first.plan_version
    assert _day_names(planned_user.id) == ["Bench Press", "Shoulder Press",
                                           "Cable Fly"]


def test_a_lost_race_with_no_winning_row_leaves_no_open_transaction(
        planned_user, monkeypatch):
    """The pathological branch: the insert fails but no journal row explains it.

    The service raises ``PlanStateConflict``; it must not also leave the
    diagnostic read hanging.
    """
    from app.services.plan_mutation import PlanStateConflict
    from app.services.plan_mutation import service as svc

    monkeypatch.setattr(svc, "find_by_key", lambda user_id, key: None)

    def explode(*_args, **_kwargs):
        from sqlalchemy.exc import IntegrityError
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(svc.db.session, "flush", explode)

    with pytest.raises(PlanStateConflict):
        apply_plan_mutation(planned_user.id, _add(), _ctx("chr-race-0004"))

    assert not open_transaction()
