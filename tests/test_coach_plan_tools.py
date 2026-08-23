"""What the AI Coach's plan-editing tools actually do to the database.

Every assertion here is about **state**: the persisted plan document, the
mutation journal, the plan version, the open-transaction flag. "The mock was
called" is not evidence that a plan changed, and — more importantly — it is not
evidence that a plan changed *once* (brief §51). Where a test drives a provider
loop it uses a scripted fake with no network, and it still checks the database
afterwards rather than the fake's call log.

Layout:

* the flag — OFF is not "the tools are hidden", it is "the tools cannot run";
* one turn, one intent — replay, distinct commands, and the per-turn budget;
* provider reality — a tool that ran is never re-run by a fallback, and a
  committed mutation survives a provider failure that comes after it;
* the error matrix — every bounded code, reached the way production reaches it;
* what must never leak into a tool result;
* what a plan edit must not touch.
"""
import json

import pytest

from app.extensions import db
from app.models import PlanMutationRecord, TrainingPlan, WorkoutLog
from app.services import ai_coach, coach_plan_tools
from app.services.coach_plan_tools import identity, results
from app.services.plan_mutation import PlanStateConflict
from tests.test_coach_plan_tool_characterization import (  # noqa: F401
    _program, open_transaction, planned_user,
)

REPLACE = "replace_training_plan_exercise"
ADD = "add_training_plan_exercise"
REMOVE = "remove_training_plan_exercise"
PRESCRIBE = "update_training_plan_prescription"
MOVE = "move_training_plan_day"
UNDO = "undo_last_training_plan_change"


# ── Fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture
def tools_on(app):
    """The rollout flag, ON for this test only."""
    previous = app.config.get(coach_plan_tools.FLAG_KEY, False)
    app.config[coach_plan_tools.FLAG_KEY] = True
    yield
    app.config[coach_plan_tools.FLAG_KEY] = previous


@pytest.fixture
def turn(app):
    """One server Coach turn: a request context with a real turn identity.

    ``assign_request_id`` is called explicitly because ``test_request_context``
    does not run before-request hooks. Without it the executor fails closed with
    TURN_IDENTITY_UNAVAILABLE — which is itself tested below, deliberately.
    """
    from app.observability import assign_request_id

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn()
        yield


def call(user_id, name, arguments=None):
    return coach_plan_tools.execute_plan_tool(user_id, name, arguments or {})


def call_and_settle(user_id, name, arguments=None):
    """``call`` plus the residual transaction state, sampled immediately.

    Sampling matters: the very next assertion in a test usually reads the plan
    back, and that read autobegins a transaction of its own. Checking afterwards
    would measure the test, not the boundary.
    """
    result = call(user_id, name, arguments)
    return result, open_transaction()


def day_exercises(user_id, day="Pazartesi"):
    """The persisted exercises of one day — the state, not a projection."""
    from app.services.today_facts import get_active_plan

    document = json.loads(get_active_plan(user_id).plan_data)
    entry = next(d for d in document["program"] if d["gun"] == day)
    return [(e["isim"], e.get("set"), e.get("tekrar"))
            for e in entry["egzersizler"]]


def names(user_id, day="Pazartesi"):
    return [exercise for exercise, _sets, _reps in day_exercises(user_id, day)]


def journal(user_id):
    return (PlanMutationRecord.query.filter_by(user_id=user_id)
            .order_by(PlanMutationRecord.id).all())


def plan_version(user_id):
    from app.services.today_facts import get_active_plan

    db.session.expire_all()
    return get_active_plan(user_id).mutation_version


def seed_plan(user_id, program):
    db.session.add(TrainingPlan(
        user_id=user_id, score=7,
        plan_data=json.dumps(program, ensure_ascii=False)))
    db.session.commit()


# ── The flag ─────────────────────────────────────────────────────────────────

def test_off_publishes_the_pre_pr3_tool_surface_to_both_providers(app):
    """OFF is byte-identical to the surface that existed before PR3."""
    with app.test_request_context("/ask"):
        openai_names = [t["function"]["name"]
                        for t in ai_coach._openai_tools_for_call()]
        anthropic_names = [t["name"]
                           for t in ai_coach._anthropic_tools_for_call()]

    canonical = [t["name"] for t in ai_coach._COACH_TOOL_DEFS]
    assert openai_names == canonical
    assert anthropic_names == canonical
    assert not set(canonical) & coach_plan_tools.PLAN_MUTATION_TOOL_NAMES


def test_on_publishes_the_same_six_tools_to_both_providers(app, tools_on):
    """One registry, two formats, one order — no provider can drift (§41)."""
    with app.test_request_context("/ask"):
        openai_tools = ai_coach._openai_tools_for_call()
        anthropic_tools = ai_coach._anthropic_tools_for_call()

    openai_names = [t["function"]["name"] for t in openai_tools]
    anthropic_names = [t["name"] for t in anthropic_tools]
    assert openai_names == anthropic_names
    assert coach_plan_tools.PLAN_MUTATION_TOOL_NAMES.issubset(openai_names)

    for definition, openai_tool, anthropic_tool in zip(
            ai_coach._coach_tool_defs_for_call(), openai_tools,
            anthropic_tools):
        assert openai_tool["function"]["parameters"] == definition["parameters"]
        assert anthropic_tool["input_schema"] == definition["parameters"]
        assert openai_tool["function"]["description"] == \
            anthropic_tool["description"]


def test_off_refuses_execution_and_leaves_the_plan_alone(
        app, planned_user, turn):
    """The flag is not only a schema decision.

    A model that saw these tools in an earlier turn — or a future caller that
    forgets the gate — must not be able to reach the mutation boundary through
    a name it remembers (§45).
    """
    before = names(planned_user.id)
    result = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Bench Press"})

    assert result["error"] == results.ERROR_CAPABILITY_DISABLED
    assert names(planned_user.id) == before
    assert journal(planned_user.id) == []


def test_the_policy_block_appears_only_when_the_tools_do(
        app, planned_user, monkeypatch):
    """A prompt must not describe a capability the model cannot reach (§64).

    Read from the system message each provider loop actually sends, not from a
    helper, so a future wiring change that skips the parameter fails here.
    """
    from app.observability import assign_request_id
    from app.prompts.system import PLAN_MUTATION_POLICY

    def _system_prompts():
        openai = _ScriptedOpenAI([])
        monkeypatch.setattr(ai_coach, "openai_client", openai)
        with app.test_request_context("/ask", method="POST"):
            assign_request_id()
            ai_coach._run_coach_conversation(
                planned_user.id, "merhaba", "", client_history=[])
            return (openai.calls[0]["messages"][0]["content"],
                    ai_coach._build_bedrock_system("", "tr"))

    _openai_only(monkeypatch)
    off_openai, off_bedrock = _system_prompts()
    app.config[coach_plan_tools.FLAG_KEY] = True
    try:
        on_openai, on_bedrock = _system_prompts()
    finally:
        app.config[coach_plan_tools.FLAG_KEY] = False

    assert PLAN_MUTATION_POLICY not in off_openai
    assert PLAN_MUTATION_POLICY not in off_bedrock
    assert PLAN_MUTATION_POLICY in on_openai
    assert PLAN_MUTATION_POLICY in on_bedrock


# ── One turn, one intent ─────────────────────────────────────────────────────

def test_a_replace_changes_exactly_one_exercise_and_records_it(
        app, planned_user, tools_on, turn):
    result, residual = call_and_settle(
        planned_user.id, REPLACE,
        {"day": "Pazartesi", "exercise": "Bench Press",
         "replacement": "Machine Press"})

    assert result["status"] == results.STATUS_APPLIED
    # Nothing is held open for the provider call that follows a tool (§43).
    assert not residual
    assert names(planned_user.id) == ["Machine Press", "Shoulder Press"]
    # The untouched day is untouched, not rebuilt.
    assert names(planned_user.id, "Cuma") == ["Squat"]
    entries = journal(planned_user.id)
    assert [(e.command_type, e.actor, e.outcome) for e in entries] == [
        ("replace_exercise", "ai_coach", "applied")]
    assert entries[0].reason is None
    assert plan_version(planned_user.id) == 1


def test_the_same_command_twice_in_one_turn_changes_the_plan_once(
        app, planned_user, tools_on, turn):
    """The failure this prevents is a duplicated exercise, not a wasted call.

    A provider that re-delivers a tool call — a retry, a malformed continuation,
    a model that repeats itself — must not add "Cable Fly" twice (§35).
    """
    arguments = {"day": "Pazartesi", "exercise": "Cable Fly",
                 "sets": 3, "reps": "12"}
    first = call(planned_user.id, ADD, dict(arguments))
    second, residual = call_and_settle(planned_user.id, ADD, dict(arguments))

    assert first["status"] == results.STATUS_APPLIED
    assert second["status"] == results.STATUS_REPLAYED
    # A replay is served from durable history; it must not leave a read open.
    assert not residual
    assert names(planned_user.id).count("Cable Fly") == 1
    assert len(journal(planned_user.id)) == 1
    assert plan_version(planned_user.id) == 1


def test_two_different_commands_in_one_turn_are_two_changes(
        app, planned_user, tools_on, turn):
    """Replay must not swallow a second, genuinely different edit (§36)."""
    first = call(planned_user.id, ADD,
                 {"day": "Pazartesi", "exercise": "Cable Fly",
                  "sets": 3, "reps": "12"})
    second = call(planned_user.id, PRESCRIBE,
                  {"day": "Cuma", "exercise": "Squat", "sets": 4})

    assert first["status"] == results.STATUS_APPLIED
    assert second["status"] == results.STATUS_APPLIED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press", "Cable Fly"]
    assert day_exercises(planned_user.id, "Cuma") == [("Squat", 4, "5")]
    keys = {entry.idempotency_key for entry in journal(planned_user.id)}
    assert len(keys) == 2
    assert plan_version(planned_user.id) == 2


def test_a_third_distinct_change_in_one_turn_is_refused(
        app, planned_user, tools_on, turn):
    """The budget bounds durable writes, not tool calls (§34).

    The refusal is what stops one misread message from becoming a mutation
    swarm, and the first two edits stay applied — a budget that rolled back
    earlier work would punish the user for the model's behaviour.
    """
    call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Cable Fly",
                                "sets": 3, "reps": "12"})
    call(planned_user.id, PRESCRIBE,
         {"day": "Cuma", "exercise": "Squat", "sets": 4})
    third = call(planned_user.id, REPLACE,
                 {"day": "Pazartesi", "exercise": "Bench Press",
                  "replacement": "Machine Press"})

    assert third["error"] == results.ERROR_MUTATION_BUDGET_EXHAUSTED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press", "Cable Fly"]
    assert day_exercises(planned_user.id, "Cuma") == [("Squat", 4, "5")]
    assert len(journal(planned_user.id)) == 2


def test_a_repeat_of_an_earlier_change_does_not_spend_budget(
        app, planned_user, tools_on, turn):
    """Otherwise a duplicate delivery would be reported as "too many changes".

    That is the worst possible answer: the user's edit WAS applied, and the
    model would tell them it was rejected.
    """
    first = {"day": "Pazartesi", "exercise": "Cable Fly",
             "sets": 3, "reps": "12"}
    call(planned_user.id, ADD, dict(first))
    call(planned_user.id, ADD, dict(first))       # replay, costs nothing
    call(planned_user.id, ADD, dict(first))       # still a replay
    second = call(planned_user.id, PRESCRIBE,
                  {"day": "Cuma", "exercise": "Squat", "sets": 4})

    assert second["status"] == results.STATUS_APPLIED
    assert len(journal(planned_user.id)) == 2


def test_a_new_turn_gets_a_new_budget_and_a_new_identity(
        app, planned_user, tools_on):
    """Two user messages asking for the same thing are two intents (§19).

    The user who says "add cable flies" twice, in two messages, has asked
    twice. Convergence is a within-turn guarantee, and this test states that
    boundary rather than letting it be assumed either way.
    """
    from app.observability import assign_request_id

    arguments = {"day": "Pazartesi", "exercise": "Cable Fly",
                 "sets": 3, "reps": "12"}
    turn_ids = []
    for _ in range(2):
        with app.test_request_context("/ask", method="POST"):
            assign_request_id()
            ai_coach._begin_coach_turn()
            turn_ids.append(identity.current_turn_id())
            assert call(planned_user.id, ADD, dict(arguments))["status"] == \
                results.STATUS_APPLIED

    assert turn_ids[0] != turn_ids[1]
    assert names(planned_user.id).count("Cable Fly") == 2
    assert len({e.idempotency_key for e in journal(planned_user.id)}) == 2


def test_undo_reverses_the_newest_change_and_only_on_request(
        app, planned_user, tools_on, turn):
    call(planned_user.id, REPLACE,
         {"day": "Pazartesi", "exercise": "Bench Press",
          "replacement": "Machine Press"})
    undone, residual = call_and_settle(planned_user.id, UNDO)

    assert undone["status"] == results.STATUS_APPLIED
    assert not residual
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    entries = journal(planned_user.id)
    assert [e.operation_kind for e in entries] == ["mutation", "undo"]
    # Append-only: the reversed entry is pointed at, never rewritten.
    assert entries[1].reverts_mutation_id == entries[0].id
    assert entries[0].outcome == "applied"
    # Undo is a new persisted state transition; the version never goes back.
    assert plan_version(planned_user.id) == 2


def test_a_repeated_undo_in_one_turn_does_not_reach_further_back(
        app, planned_user, tools_on, turn):
    """A duplicated undo delivery must not quietly reverse a SECOND change.

    Undo takes no arguments, so every undo in one turn is the same operation —
    which is exactly why the second delivery replays instead of walking the
    history backwards (§52).
    """
    call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Cable Fly",
                                "sets": 3, "reps": "12"})
    first = call(planned_user.id, UNDO)
    second = call(planned_user.id, UNDO)

    assert first["status"] == results.STATUS_APPLIED
    assert second["status"] == results.STATUS_REPLAYED
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert [e.operation_kind for e in journal(planned_user.id)] == [
        "mutation", "undo"]


def test_undo_refuses_arguments_instead_of_ignoring_them(
        app, planned_user, tools_on, turn):
    """A model that thinks it can choose WHICH change to undo must learn it
    cannot — silently dropping the argument would execute a different
    operation than the one the model expressed (§31, §38)."""
    call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Cable Fly",
                                "sets": 3, "reps": "12"})
    refused = call(planned_user.id, UNDO, {"version": 1})

    assert refused["error"] == results.ERROR_INVALID_ARGUMENTS
    assert "version" in refused["detail"]
    assert names(planned_user.id).count("Cable Fly") == 1
    assert [e.operation_kind for e in journal(planned_user.id)] == ["mutation"]


# ── Provider reality ─────────────────────────────────────────────────────────

class _ScriptedOpenAI:
    """A deterministic OpenAI client: a fixed script of tool calls, then text."""

    def __init__(self, script):
        from types import SimpleNamespace

        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                index = len(outer.calls) - 1
                if index < len(script):
                    name, arguments = script[index]
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=[
                            SimpleNamespace(
                                id=f"call_{index}",
                                function=SimpleNamespace(
                                    name=name,
                                    arguments=json.dumps(arguments)))]))])
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="tamam", tool_calls=None))])

        self.chat = SimpleNamespace(completions=_Completions())


def _openai_only(monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)


def test_the_openai_loop_applies_one_change_for_one_intent(
        app, planned_user, tools_on, monkeypatch):
    """The whole turn, through the real provider loop — state, not call counts."""
    from app.observability import assign_request_id

    _openai_only(monkeypatch)
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedOpenAI([
        (REPLACE, {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"}),
    ]))

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        answer = ai_coach._run_coach_conversation(
            planned_user.id, "Pazartesi bench yerine machine press koy",
            "", client_history=[])

    assert answer == "tamam"
    assert names(planned_user.id) == ["Machine Press", "Shoulder Press"]
    assert len(journal(planned_user.id)) == 1


def test_a_model_that_repeats_itself_still_changes_the_plan_once(
        app, planned_user, tools_on, monkeypatch):
    """Two identical tool calls inside ONE provider loop (§35)."""
    from app.observability import assign_request_id

    arguments = {"day": "Pazartesi", "exercise": "Cable Fly",
                 "sets": 3, "reps": "12"}
    _openai_only(monkeypatch)
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedOpenAI([
        (ADD, dict(arguments)), (ADD, dict(arguments)),
    ]))

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._run_coach_conversation(
            planned_user.id, "pazartesiye cable fly ekle", "",
            client_history=[])

    assert names(planned_user.id).count("Cable Fly") == 1
    assert len(journal(planned_user.id)) == 1


def test_a_provider_fallback_does_not_repeat_a_change_that_already_ran(
        app, planned_user, tools_on, monkeypatch):
    """The B-rule, with a durable write behind it.

    Bedrock runs the plan tool and then fails. Switching to OpenAI would re-run
    the model's whole turn against a plan that has ALREADY changed, and the
    second provider has no way to know that. The turn degrades to a soft error
    instead — and the mutation stays committed, because a failed continuation
    is not a reason to reverse a change the user asked for (§32, §42).
    """
    from types import SimpleNamespace

    from app.observability import assign_request_id

    tool_block = SimpleNamespace(
        type="tool_use", name=REPLACE, id="t1",
        input={"day": "Pazartesi", "exercise": "Bench Press",
               "replacement": "Machine Press"})
    responses = [SimpleNamespace(stop_reason="tool_use", content=[tool_block])]

    class _Messages:
        def create(self, **kwargs):
            if responses:
                return responses.pop(0)
            raise RuntimeError("bedrock down after the tool ran")

    openai = _ScriptedOpenAI([])
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(ai_coach, "openai_client", openai)

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        answer = ai_coach._run_coach_conversation(
            planned_user.id, "bench yerine machine press", "",
            client_history=[], language="tr")

    # The degraded turn must not say "I couldn't complete that": the change IS
    # committed, and that sentence asks the user to retry — a new request, a
    # new turn identity, a new operation key, the same exercise added twice.
    assert answer == ai_coach._COACH_FALLBACKS["tr"]["tool_plan_saved"]
    assert answer != ai_coach._COACH_FALLBACKS["tr"]["tool"]
    assert openai.calls == []                     # no second provider ran
    assert names(planned_user.id) == ["Machine Press", "Shoulder Press"]
    entries = journal(planned_user.id)
    assert [e.operation_kind for e in entries] == ["mutation"]
    assert plan_version(planned_user.id) == 1


def test_a_fallback_before_any_tool_ran_applies_the_change_once(
        app, planned_user, tools_on, monkeypatch):
    """Bedrock dies before doing anything; OpenAI completes the same turn.

    The turn identity is the HTTP request, not the provider, so the operation
    key OpenAI mints is the one Bedrock would have minted — a repeated delivery
    across the switch replays instead of applying twice.
    """
    from types import SimpleNamespace

    from app.observability import assign_request_id

    arguments = {"day": "Pazartesi", "exercise": "Cable Fly",
                 "sets": 3, "reps": "12"}

    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("bedrock down before any work")

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=_Messages()))
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedOpenAI([
        (ADD, dict(arguments)), (ADD, dict(arguments)),
    ]))

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        answer = ai_coach._run_coach_conversation(
            planned_user.id, "cable fly ekle", "", client_history=[])

    assert answer == "tamam"
    assert names(planned_user.id).count("Cable Fly") == 1
    assert len(journal(planned_user.id)) == 1


def test_a_degraded_turn_without_a_plan_change_keeps_the_old_wording(
        app, planned_user, tools_on, monkeypatch):
    """The plan-saved wording must not leak into ordinary tool failures.

    A read-only tool ran and the provider then died. Nothing was written, so
    telling the user their plan was saved would be the mirror-image lie.
    """
    from types import SimpleNamespace

    from app.observability import assign_request_id

    block = SimpleNamespace(
        type="tool_use", name="query_fitx_metrics", id="t1",
        input={"metric_type": "calories", "date_range": "today"})
    responses = [SimpleNamespace(stop_reason="tool_use", content=[block])]

    class _Messages:
        def create(self, **kwargs):
            if responses:
                return responses.pop(0)
            raise RuntimeError("bedrock down after a read-only tool")

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=_Messages()))

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn()
        answer = ai_coach._run_coach_conversation_bedrock(
            planned_user.id, "kaç kalori aldım?", "", [], "tr")

    assert answer == ai_coach._COACH_FALLBACKS["tr"]["tool"]
    assert journal(planned_user.id) == []


def test_a_no_op_is_not_reported_as_a_saved_plan_change(
        app, planned_user, tools_on, turn):
    """`no_op` moved nothing, so the degraded turn keeps the plain wording."""
    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Bench Press"})

    assert result["status"] == results.STATUS_NO_OP
    assert coach_plan_tools.plan_changed_this_turn() is False
    assert ai_coach._coach_tool_fallback("tr") == \
        ai_coach._COACH_FALLBACKS["tr"]["tool"]


def test_a_refused_plan_tool_is_not_reported_as_a_saved_plan_change(
        app, planned_user, tools_on, turn):
    refused = call(planned_user.id, REPLACE,
                   {"day": "Pazartesi", "exercise": "Deadlift",
                    "replacement": "X"})

    assert refused["error"] == results.ERROR_TARGET_NOT_FOUND
    assert coach_plan_tools.plan_changed_this_turn() is False


def test_the_plan_saved_wording_is_still_an_error_fallback(app):
    """Quota refund and the B16 do-not-persist rule must not change with it."""
    from app.services.response_formatter import is_coach_error_fallback

    for language in ("tr", "en"):
        text = ai_coach._COACH_FALLBACKS[language]["tool_plan_saved"]
        assert is_coach_error_fallback(text) is True


def test_the_streaming_path_reports_a_saved_plan_change_too(
        app, planned_user, tools_on, monkeypatch):
    """§40 parity: the honest wording cannot exist only in blocking mode."""
    from types import SimpleNamespace

    from app.observability import assign_request_id
    from app.services import ai_stream

    block = SimpleNamespace(
        type="tool_use", name=ADD, id="t1",
        input={"day": "Pazartesi", "exercise": "Cable Fly", "sets": 3,
               "reps": "12"})
    final = SimpleNamespace(stop_reason="tool_use", content=[block],
                            usage=None)
    state = {"n": 0}

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        text_stream = ()

        def get_final_message(self):
            return final

    class _Messages:
        def stream(self, **_kwargs):
            state["n"] += 1
            if state["n"] == 1:
                return _Stream()
            raise RuntimeError("bedrock stream died after the tool ran")

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(ai_coach, "bedrock_client",
                        SimpleNamespace(messages=_Messages()))

    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        events = list(ai_stream.stream_coach_answer(
            planned_user.id, "cable fly ekle", "", [], "tr"))

    error = [e for e in events if e["type"] == "error"]
    assert error, events
    assert error[-1]["key"] == "coach.reply_failed_plan_saved"
    assert error[-1]["work_performed"] is True
    assert names(planned_user.id).count("Cable Fly") == 1
    assert len(journal(planned_user.id)) == 1


def test_both_locales_carry_the_saved_plan_message():
    import io
    import json

    for language in ("tr", "en"):
        with io.open(f"locales/{language}.json", encoding="utf-8") as handle:
            catalogue = json.load(handle)
        assert catalogue["coach.reply_failed_plan_saved"].strip()


def test_an_unknown_tool_name_never_reaches_the_mutation_boundary(
        app, planned_user, tools_on, turn):
    raw = ai_coach._dispatch_coach_tool(
        planned_user.id, "mutate_training_plan", '{"op": "delete_everything"}')

    assert json.loads(raw)["status"] == "error"
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert journal(planned_user.id) == []


def test_unparseable_arguments_never_execute_an_undo(
        app, planned_user, tools_on, turn):
    """The dispatcher folds malformed JSON to ``{}`` for the legacy tools.

    Undo accepts ``{}``, so inheriting that behaviour would turn a decoding
    failure into a plan change nobody requested (§37).
    """
    call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Cable Fly",
                                "sets": 3, "reps": "12"})
    raw = ai_coach._dispatch_coach_tool(planned_user.id, UNDO, "{not json")

    assert json.loads(raw)["error"] == results.ERROR_INVALID_ARGUMENTS
    assert names(planned_user.id).count("Cable Fly") == 1
    assert [e.operation_kind for e in journal(planned_user.id)] == ["mutation"]


# ── The error matrix ─────────────────────────────────────────────────────────

def test_no_active_plan(app, make_user, tools_on, turn):
    user = make_user("planless")
    result = call(user.id, REMOVE, {"day": "Pazartesi", "exercise": "Squat"})
    assert result["error"] == results.ERROR_PLAN_NOT_FOUND
    assert not open_transaction()


def test_an_unreadable_plan_is_refused_not_repaired(app, make_user, tools_on,
                                                    turn):
    user = make_user("brokenplan")
    db.session.add(TrainingPlan(user_id=user.id, score=5,
                                plan_data="{ not a plan"))
    db.session.commit()

    result = call(user.id, REMOVE, {"day": "Pazartesi", "exercise": "Squat"})

    assert result["error"] == results.ERROR_PLAN_NOT_MUTABLE
    # Refused, not repaired and not regenerated: the row is exactly as it was.
    assert TrainingPlan.query.filter_by(user_id=user.id).one().plan_data == \
        "{ not a plan"
    assert journal(user.id) == []


def test_unknown_day(app, planned_user, tools_on, turn):
    """A day outside the schema's enum — an English weekday is the realistic
    way a model produces one."""
    result, residual = call_and_settle(
        planned_user.id, REMOVE, {"day": "Monday", "exercise": "Squat"})
    assert result["error"] == results.ERROR_DAY_NOT_FOUND
    assert not residual


def test_unknown_exercise(app, planned_user, tools_on, turn):
    result = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Deadlift"})
    assert result["error"] == results.ERROR_TARGET_NOT_FOUND
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_an_ambiguous_target_is_refused_never_guessed(
        app, make_user, tools_on, turn):
    """Picking "the first one" would silently edit an exercise the user did not
    name, and neither the user nor the model would find out (§23)."""
    program = _program()
    program["program"][0]["egzersizler"] = [
        {"isim": "Bench Press", "set": 3, "tekrar": "8-12"},
        {"isim": "bench press", "set": 4, "tekrar": "5"},
    ]
    user = make_user("ambiguous")
    seed_plan(user.id, program)

    result = call(user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Bench Press"})

    assert result["error"] == results.ERROR_AMBIGUOUS_TARGET
    assert len(day_exercises(user.id)) == 2
    assert journal(user.id) == []


def test_adding_to_a_rest_day_is_refused(app, planned_user, tools_on, turn):
    result = call(planned_user.id, ADD,
                  {"day": "Salı", "exercise": "Squat", "sets": 3, "reps": "8"})
    assert result["error"] == results.ERROR_INVALID_MUTATION
    assert day_exercises(planned_user.id, "Salı") == []


def test_emptying_a_training_day_is_refused(app, planned_user, tools_on, turn):
    result = call(planned_user.id, REMOVE,
                  {"day": "Cuma", "exercise": "Squat"})
    assert result["error"] == results.ERROR_INVALID_MUTATION
    assert names(planned_user.id, "Cuma") == ["Squat"]


def test_an_out_of_range_prescription_is_refused_not_clamped(
        app, planned_user, tools_on, turn):
    """Writing 100 sets for a user who asked for 999 would report success for a
    request that was never honoured."""
    result = call(planned_user.id, PRESCRIBE,
                  {"day": "Cuma", "exercise": "Squat", "sets": 999})
    assert result["error"] == results.ERROR_INVALID_PRESCRIPTION
    assert day_exercises(planned_user.id, "Cuma") == [("Squat", 5, "5")]


@pytest.mark.parametrize("name,arguments,expected", [
    (REPLACE, {"day": "Pazartesi", "exercise": "Bench Press"}, "eksik"),
    (REPLACE, {"day": "Pazartesi", "exercise": "Bench Press",
               "replacement": "X", "plan_id": 4}, "beklenmeyen"),
    (ADD, {"day": "Pazartesi", "exercise": "X", "sets": True, "reps": "8"},
     "tam sayı"),
    (ADD, {"day": "Pazartesi", "exercise": "X", "sets": "3", "reps": "8"},
     "tam sayı"),
    (ADD, {"day": "Pazartesi", "exercise": "   ", "sets": 3, "reps": "8"},
     "boş"),
    (PRESCRIBE, {"day": "Cuma", "exercise": "Squat"}, "en az biri"),
    (MOVE, {"day": "Cuma"}, "eksik"),
])
def test_malformed_arguments_are_refused_before_any_domain_work(
        app, planned_user, tools_on, turn, name, arguments, expected):
    """The schema is a hint to the model; this is the boundary (§37).

    Each case also proves no operation identity was spent: the journal stays
    empty, so a corrected retry in the same turn is a fresh operation.
    """
    result = call(planned_user.id, name, arguments)

    assert result["error"] == results.ERROR_INVALID_ARGUMENTS
    assert expected in result["detail"]
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert journal(planned_user.id) == []


def test_a_non_object_argument_payload_is_refused(
        app, planned_user, tools_on, turn):
    result = coach_plan_tools.execute_plan_tool(
        planned_user.id, REMOVE, "day=Pazartesi")
    assert result["error"] == results.ERROR_INVALID_ARGUMENTS
    assert journal(planned_user.id) == []


def test_nothing_to_undo(app, planned_user, tools_on, turn):
    result = call(planned_user.id, UNDO)
    assert result["error"] == results.ERROR_UNDO_UNAVAILABLE
    assert "SÖYLEME" in result["message"]
    assert not open_transaction()


def test_undo_refuses_when_the_plan_moved_underneath_it(
        app, planned_user, tools_on, turn):
    """The undo precondition is the stored bytes, not the version number.

    Here the legacy full-plan path replaced the document after the mutation, so
    restoring the old snapshot would resurrect a plan the user has since
    replaced.
    """
    from app.services.today_facts import get_active_plan

    call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Cable Fly",
                                "sets": 3, "reps": "12"})
    plan = get_active_plan(planned_user.id)
    document = json.loads(plan.plan_data)
    document["program"][0]["egzersizler"].append(
        {"isim": "Dip", "set": 3, "tekrar": "10"})
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()

    result = call(planned_user.id, UNDO)

    assert result["error"] == results.ERROR_UNDO_CONFLICT
    assert "Dip" in names(planned_user.id)


def test_an_operation_key_bound_to_a_different_change_fails_closed(
        app, planned_user, tools_on, turn):
    """Two different intents must never collapse into one replay.

    Reproduced by binding the key this command WILL mint to a different command
    type, which is the shape a digest collision or a key-reuse bug would take.
    """
    from app.services.plan_mutation import AddExerciseCommand

    command = AddExerciseCommand(
        day="Pazartesi", exercise="Cable Fly", sets=3, reps="12")
    key = identity.mutation_operation_key(command)
    db.session.add(PlanMutationRecord(
        public_id="seeded-conflict", user_id=planned_user.id,
        plan_lineage_id="lineage", operation_kind="mutation",
        command_type="remove_exercise", command_fingerprint="other",
        actor="ai_coach", outcome="applied", before_version=0, after_version=1,
        before_snapshot="{}", after_snapshot="{}",
        before_fingerprint="a", after_fingerprint="b", idempotency_key=key))
    db.session.commit()

    result = call(planned_user.id, ADD,
                  {"day": "Pazartesi", "exercise": "Cable Fly",
                   "sets": 3, "reps": "12"})

    assert result["error"] == results.ERROR_IDEMPOTENCY_CONFLICT
    assert "Cable Fly" not in names(planned_user.id)


def test_a_lost_write_race_is_reported_not_narrated(
        app, planned_user, tools_on, turn, monkeypatch):
    """``PlanStateConflict`` only arises from a genuine concurrent write, so it
    is reached here by raising it at the boundary. What is under test is the
    translation and the recovery instruction, not the race — the race itself is
    covered against real PostgreSQL in tests/test_plan_mutation_history_pg.py.
    """
    def _conflict(*_args, **_kwargs):
        raise PlanStateConflict("the plan changed")

    monkeypatch.setattr(
        "app.services.coach_plan_tools.executor.apply_plan_mutation",
        _conflict)

    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"})

    assert result["error"] == results.ERROR_PLAN_STATE_CONFLICT
    assert "Zorla" in result["message"]
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]


def test_an_unexpected_failure_is_never_dressed_up_as_user_error(
        app, planned_user, tools_on, turn, monkeypatch):
    """Telling a user to "check the exercise name" when the database is down
    sends them in circles (§73)."""
    def _boom(*_args, **_kwargs):
        raise ValueError("something internal")

    monkeypatch.setattr(
        "app.services.coach_plan_tools.executor.apply_plan_mutation", _boom)

    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"})

    assert result["error"] == results.ERROR_INTERNAL
    assert "something internal" not in json.dumps(result, ensure_ascii=False)


def test_an_unexpected_failure_does_not_log_the_exception_it_swallowed(
        app, planned_user, tools_on, turn, monkeypatch, caplog):
    """The internal-error path is the one that can leak the plan into a log.

    A traceback is not deliberately audit material, but it carries the
    exception's own message — and the realistic unexpected failure here is a
    SQLAlchemy ``StatementError``, whose message embeds the statement and its
    bound parameters, i.e. the whole plan document. So the handler logs the
    exception *class*, never the exception (§60).
    """
    def _boom(*_args, **_kwargs):
        raise ValueError("Bench Press -> Incline Press, plan_data={...}")

    monkeypatch.setattr(
        "app.services.coach_plan_tools.executor.apply_plan_mutation", _boom)

    with caplog.at_level("WARNING"):
        call(planned_user.id, REPLACE,
             {"day": "Pazartesi", "exercise": "Bench Press",
              "replacement": "Machine Press"})

    emitted = "\n".join(
        record.getMessage() + (record.exc_text or "")
        for record in caplog.records)
    assert "ValueError" in emitted          # enough to route a diagnosis
    assert "plan_data" not in emitted
    assert "Incline Press" not in emitted
    assert "Bench Press" not in emitted


def test_a_boolean_principal_is_refused_instead_of_resolving_to_user_one(
        app, planned_user, tools_on, turn):
    """``bool`` is an ``int`` subclass, so a sloppy ``isinstance`` check would
    let ``True`` through and then mutate whichever user has id 1 — the single
    mistake this whole boundary exists to make impossible."""
    before = names(planned_user.id)

    result = call(True, REMOVE, {"day": "Pazartesi", "exercise": "Bench Press"})

    assert result["error"] == results.ERROR_INTERNAL
    assert names(planned_user.id) == before
    assert PlanMutationRecord.query.count() == 0


def test_without_a_turn_identity_the_tools_refuse(app, planned_user, tools_on):
    """No request, no turn — and no key that would be safe to mint (§16)."""
    with app.app_context():
        result = call(planned_user.id, ADD,
                      {"day": "Pazartesi", "exercise": "Cable Fly",
                       "sets": 3, "reps": "12"})

    assert result["error"] == results.ERROR_TURN_IDENTITY_UNAVAILABLE
    assert "Cable Fly" not in names(planned_user.id)
    assert journal(planned_user.id) == []


def test_every_error_code_carries_recovery_guidance(app):
    """A bounded vocabulary is only useful if each code says what to do next."""
    codes = {value for name, value in vars(results).items()
             if name.startswith("ERROR_")}
    for code in codes:
        message = results.error_result(code)["message"]
        assert message
        assert message == results._ERROR_MESSAGES[code]


# ── What must never leave this boundary ──────────────────────────────────────

def test_a_tool_result_carries_no_server_identity(
        app, planned_user, tools_on, turn):
    """Snapshots, keys, fingerprints, row ids and the lineage stay server-side.

    Anything in a tool result is billed, re-read by the model, and liable to be
    paraphrased to the user (§25).
    """
    from app.services.today_facts import get_active_plan

    applied = call(planned_user.id, REPLACE,
                   {"day": "Pazartesi", "exercise": "Bench Press",
                    "replacement": "Machine Press"})
    undone = call(planned_user.id, UNDO)

    entry = journal(planned_user.id)[0]
    plan = get_active_plan(planned_user.id)
    for payload in (applied, undone):
        assert set(payload) <= {"status", "operation", "plan_version",
                                "change", "summary", "note"}
        blob = json.dumps(payload, ensure_ascii=False)
        for secret in (entry.idempotency_key, entry.command_fingerprint,
                       entry.public_id, entry.after_fingerprint,
                       plan.lineage_id):
            assert secret not in blob
        assert "mutation_id" not in payload      # opaque, but still withheld
        assert "Shoulder Press" not in blob      # the whole plan never travels


def test_an_error_result_never_echoes_the_domain_exception(
        app, planned_user, tools_on, turn):
    result = call(planned_user.id, REMOVE,
                  {"day": "Pazartesi", "exercise": "Deadlift"})
    assert set(result) == {"status", "error", "message"}
    assert result["message"] == results._ERROR_MESSAGES[
        results.ERROR_TARGET_NOT_FOUND]


def test_a_no_op_is_reported_as_a_no_op(app, planned_user, tools_on, turn):
    """"Already true" is an accepted request and NOT a change. Reporting it as
    one would have the coach narrate an edit that never happened."""
    result = call(planned_user.id, PRESCRIBE,
                  {"day": "Cuma", "exercise": "Squat", "sets": 5})

    assert result["status"] == results.STATUS_NO_OP
    assert "SÖYLEME" in result["note"]
    assert plan_version(planned_user.id) == 0
    assert day_exercises(planned_user.id, "Cuma") == [("Squat", 5, "5")]


def test_an_applied_result_flags_the_stale_context_block(
        app, planned_user, tools_on, turn):
    """The plan block in the prompt predates the tool call (§33)."""
    result = call(planned_user.id, REPLACE,
                  {"day": "Pazartesi", "exercise": "Bench Press",
                   "replacement": "Machine Press"})
    assert result["status"] == results.STATUS_APPLIED
    assert "bu sonucu esas al" in result["note"]


# ── What a plan edit must not touch ──────────────────────────────────────────

def test_a_coach_edit_does_not_touch_the_workout_session_or_history(
        app, planned_user, tools_on, turn):
    """Completed history and session state have their own owners (§58, §59).

    Refreshing a session fingerprint here would make this boundary a second
    writer of workout-session state; rewriting a WorkoutLog would rewrite
    what the user actually did.
    """
    from app.models import WorkoutSession
    from app.services.today_facts import get_active_plan
    from app.timeutil import day_key

    session_row = WorkoutSession(
        user_id=planned_user.id, status="active", workout_date=day_key(),
        weekday_slot="Pazartesi", source="scheduled",
        planned_training_plan_id=get_active_plan(planned_user.id).id,
        plan_fingerprint="v1:" + "c" * 64)
    log = WorkoutLog(user_id=planned_user.id, exercise_name="Bench Press",
                     sets=3, reps=10, weight_kg=80, volume=2400)
    db.session.add_all([session_row, log])
    db.session.commit()
    before_session = (session_row.plan_fingerprint, session_row.status,
                      session_row.version, session_row.updated_at,
                      session_row.planned_training_plan_id)
    before_log = (log.exercise_name, log.sets, log.reps, log.volume)

    call(planned_user.id, PRESCRIBE,
         {"day": "Cuma", "exercise": "Squat", "sets": 4})
    call(planned_user.id, UNDO)

    db.session.expire_all()
    session_row = WorkoutSession.query.filter_by(
        user_id=planned_user.id).one()
    log = WorkoutLog.query.filter_by(user_id=planned_user.id).one()
    assert (session_row.plan_fingerprint, session_row.status,
            session_row.version, session_row.updated_at,
            session_row.planned_training_plan_id) == before_session
    assert (log.exercise_name, log.sets, log.reps, log.volume) == before_log


def test_the_existing_coach_tools_are_unaffected_by_the_flag(
        app, auth_user, tools_on, turn, monkeypatch):
    """PR3 adds a surface; it must not change the eight that were there (§49)."""
    monkeypatch.setattr(
        ai_coach, "_coach_search_food",
        lambda q: [{"name": "Tavuk", "food_id": "1",
                    "per_100g": {"calories": 165.0, "protein": 31.0,
                                 "carbs": 0.0, "fat": 3.6}}])
    staged = json.loads(ai_coach._dispatch_coach_tool(
        auth_user.id, "fetch_nutrition_and_stage_log",
        json.dumps({"food_query": "100g tavuk"})))

    assert staged["status"] == "staged"


# ── Sprint 11 PR4 Task 5: the Coach door obeys the catalog too ───────────────
#
# The whole claim of PR4 is that the catalog is the single authority on exercise
# identity *no matter which door a write comes through*. Task 4 shut the save
# door. These tests drive the Adaptive Coaching door the way production drives
# it — the real executor, the real flag, the real turn identity — and check the
# database afterwards, not the domain layer's return value.

def _canonical_program():
    """A plan carrying the ``exercise_context`` the Task 4 save boundary writes."""
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Üst Vücut",
             "sure_dk": 45, "tahmini_kalori": 320, "egzersizler": [
                 {"isim": "Dumbbell Row", "set": 3, "tekrar": "8-12",
                  "dinlenme": "90 sn", "not": "",
                  "exercise_id": "ex_dumbbell_row"},
                 {"isim": "Push-Up", "set": 3, "tekrar": "10-15",
                  "dinlenme": "60 sn", "not": "",
                  "exercise_id": "ex_push_up"}]},
            {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Alt Vücut",
             "sure_dk": 40, "tahmini_kalori": 300, "egzersizler": [
                 {"isim": "Bodyweight Squat", "set": 4, "tekrar": "12-15",
                  "dinlenme": "60 sn", "not": "",
                  "exercise_id": "ex_bodyweight_squat"}]},
            {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Merkez", "sure_dk": 30,
             "tahmini_kalori": 200, "egzersizler": [
                 {"isim": "Plank", "set": 3, "tekrar": "45 sn",
                  "dinlenme": "45 sn", "not": "", "exercise_id": "ex_plank"}]},
            {"gun": "Cumartesi", "tip": "kardiyo", "odak": "Yürüyüş",
             "sure_dk": 30, "tahmini_kalori": 250, "egzersizler": [
                 {"isim": "Brisk Walk", "set": 1, "tekrar": "30 dk",
                  "dinlenme": "-", "not": "",
                  "exercise_id": "ex_brisk_walk"}]},
            {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma",
             "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        ],
        "haftalik_ozet": {"toplam_antrenman_gun": 4, "yogunluk_skoru": 6},
        "exercise_context": {
            "equipment_context": "minimal", "cardio_type": "yuruyus",
            "style": "general_fitness", "catalog_version": 1},
    }


@pytest.fixture
def canonical_user(app, make_user):
    """A user holding one canonical, catalog-owned active plan."""
    user = make_user("canonicalcoach")
    seed_plan(user.id, _canonical_program())
    return user


def identities(user_id, day="Pazartesi"):
    from app.services.today_facts import get_active_plan

    db.session.expire_all()
    document = json.loads(get_active_plan(user_id).plan_data)
    entry = next(d for d in document["program"] if d["gun"] == day)
    return [(e["isim"], e.get("exercise_id")) for e in entry["egzersizler"]]


def test_a_coach_edit_on_a_canonical_plan_persists_catalog_identity(
        app, canonical_user, tools_on, turn):
    """The model names an alias; the plan stores the catalog's answer, ID and
    canonical name together."""
    result = call(canonical_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "One-Arm Dumbbell Row",
        "replacement": "Band Row"})

    assert result["status"] == results.STATUS_APPLIED
    assert identities(canonical_user.id) == [
        ("Resistance Band Row", "ex_band_row"), ("Push-Up", "ex_push_up")]


def test_a_coach_added_exercise_on_a_canonical_plan_carries_identity(
        app, canonical_user, tools_on, turn):
    result = call(canonical_user.id, ADD, {
        "day": "Çarşamba", "exercise": "Band Row", "sets": 3, "reps": "10-12"})

    assert result["status"] == results.STATUS_APPLIED
    assert identities(canonical_user.id, "Çarşamba")[-1] == (
        "Resistance Band Row", "ex_band_row")


def test_the_coach_cannot_invent_an_exercise_on_a_canonical_plan(
        app, canonical_user, tools_on, turn):
    """A plausible-sounding name the catalog does not declare is not an
    exercise. No fuzzy match, no nearest neighbour, no substitution."""
    result = call(canonical_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "Dumbbell Row",
        "replacement": "Machine Chest Press"})

    assert result["error"] == results.ERROR_INVALID_MUTATION
    assert identities(canonical_user.id) == [
        ("Dumbbell Row", "ex_dumbbell_row"), ("Push-Up", "ex_push_up")]
    assert journal(canonical_user.id) == []
    assert plan_version(canonical_user.id) == 0


def test_the_coach_cannot_prescribe_equipment_the_plan_does_not_have(
        app, canonical_user, tools_on, turn):
    """``minimal`` is bodyweight, dumbbell and bands. A barbell squat is a real
    catalog entry and still must not reach this user's plan."""
    result = call(canonical_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "Dumbbell Row",
        "replacement": "Barbell Back Squat"})

    assert result["error"] == results.ERROR_INVALID_MUTATION
    assert identities(canonical_user.id) == [
        ("Dumbbell Row", "ex_dumbbell_row"), ("Push-Up", "ex_push_up")]
    assert journal(canonical_user.id) == []


def test_the_coach_cannot_put_cardio_on_a_strength_day(
        app, canonical_user, tools_on, turn):
    """Addendum §B, at the second door. ``is_exercise_compatible`` gates cardio
    by ``cardio_type`` rather than by equipment, so a cardio movement dropped
    into a strength day would clear the equipment gate on placement alone —
    exactly the P1 Task 4 closed on the save path. If Adaptive Coaching left it
    open, PR4's single-authority claim would be false."""
    result = call(canonical_user.id, ADD, {
        "day": "Cuma", "exercise": "Brisk Walk", "sets": 1, "reps": "30 dk"})

    assert result["error"] == results.ERROR_INVALID_MUTATION
    assert names(canonical_user.id, "Cuma") == ["Plank"]
    assert journal(canonical_user.id) == []


def test_the_same_cardio_exercise_is_accepted_on_the_cardio_day(
        app, make_user, tools_on, turn):
    """The paired control, so the refusal above is placement and not a blanket
    ban on cardio edits."""
    program = _canonical_program()
    program["exercise_context"]["cardio_type"] = "karisik"
    user = make_user("canonicalcardio")
    seed_plan(user.id, program)

    result = call(user.id, ADD, {
        "day": "Cumartesi", "exercise": "Jump Rope", "sets": 1,
        "reps": "10 dk"})

    assert result["status"] == results.STATUS_APPLIED
    assert identities(user.id, "Cumartesi")[-1] == ("Jump Rope", "ex_jump_rope")


def test_a_coach_edit_leaves_a_legacy_plan_name_only(
        app, planned_user, tools_on, turn):
    """The Coach must not upgrade a pre-PR4 plan on the way past. ``Machine
    Press`` is not in the catalog and stays perfectly writable here."""
    result = call(planned_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "Bench Press",
        "replacement": "Machine Press"})

    from app.services.today_facts import get_active_plan
    db.session.expire_all()
    document = json.loads(get_active_plan(planned_user.id).plan_data)

    assert result["status"] == results.STATUS_APPLIED
    assert "exercise_context" not in document
    assert all(
        "exercise_id" not in exercise
        for day in document["program"] for exercise in day["egzersizler"])


def test_no_canonical_tool_result_leaks_catalog_identity_to_the_model(
        app, canonical_user, tools_on, turn):
    """A tool result is the next model call's input and can be paraphrased to
    the user. Catalog identity must not ride out on either path.

    The applied case is the one that can realistically regress: the result
    echoes the targets the caller named, and "helpfully" adding the resolved
    ``exercise_id`` next to them would put ``ex_*`` strings in the context
    window and hand the model an identifier it cannot use but can repeat.
    """
    applied = call(canonical_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "One-Arm Dumbbell Row",
        "replacement": "Band Row"})
    refused = call(canonical_user.id, REPLACE, {
        "day": "Pazartesi", "exercise": "Band Row",
        "replacement": "Barbell Back Squat"})

    assert applied["status"] == results.STATUS_APPLIED
    assert refused["error"] == results.ERROR_INVALID_MUTATION
    for blob in (json.dumps(applied, ensure_ascii=False),
                 json.dumps(refused, ensure_ascii=False)):
        assert "ex_" not in blob
        assert "catalog" not in blob.lower()
        assert "equipment_context" not in blob
