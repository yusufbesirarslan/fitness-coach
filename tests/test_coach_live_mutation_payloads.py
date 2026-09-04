"""The payloads production actually produced, replayed through the real server.

PR #279 made the parser and ``grounding`` able to accept a partial mutation
request: ``day``, ``sets`` and ``reps`` became server-groundable, so a call that
omits them is stored and the missing half is asked for. What it did not change
is the schema *published to the provider*, which still listed those fields as
``required`` and told the model, in the ADD description, to ask the user first
rather than call the tool without them.

So the model obeyed the contract it was given. It never called the tool, the
server never got a request to store, and the "4 sets" the user had already
typed died with the turn:

    A. "Add Walking Lunges with 4 sets to my leg workout" → the model asked for
       reps itself → "15" → the server, with nothing stored, asked for sets AND
       reps and offered 3x8-12.
    B/C/D. the model asked which day / whether to proceed, and
       ``grounded_provider_reply`` replaced that question wholesale with
       "I couldn't prepare that plan change… please repeat the exercise, day,
       sets, and reps" — the guard could not tell a request for a missing
       discriminator from a claim that a proposal was pending.

Every ``_LIVE_*`` payload below is the verbatim tool call the production model
(Bedrock, ``global.anthropic.claude-sonnet-4-5``) emitted through ``/ask/stream``
against a seeded plan, captured during reproduction. They are pinned as data,
not paraphrased into idealised dicts, because the argument SHAPE is what the
incident was about — note that A's first call carries ``sets`` and simply omits
``reps``, which is the exact shape the old schema forbade the model to send.

Persistence is asserted on the plan row, the version counter and the mutation
journal. Nothing here mocks the parser, the grounding boundary or the store.

    python -m pytest tests/test_coach_live_mutation_payloads.py -v
"""
import json

import pytest

from app.extensions import db
from app.models import (
    PendingAction,
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
    WorkoutLog,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_confirmation
from app.services.coach_plan_tools import clarifications, parser, results, schemas
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, PRESCRIBE, REPLACE, call, seed_plan, tools_on,
)


# ── The captured production payloads ────────────────────────────────────────

#: A, turn 1. "Add Walking Lunges with 4 sets to my leg workout".
#: ``reps`` is ABSENT — the user did not say it and the model no longer invents
#: it. Under the published schema this PR replaces, ``reps`` was required and
#: the model answered by not calling the tool at all.
_LIVE_ADD_PARTIAL = {
    "day": "Perşembe", "exercise": "Walking Lunges", "sets": 4,
}

#: B, turn 1. "Add Dumbbell Biceps Curl 3x10 to my arm workout" against a plan
#: with two Arms days. The model picks ONE day; the server must not take that
#: guess, because the user named a nickname matching both.
_LIVE_ADD_AMBIGUOUS = {
    "day": "Pazartesi", "exercise": "Dumbbell Biceps Curl",
    "sets": 3, "reps": "10",
}

#: C. "Replace Barbell Curl with Dumbbell Curl in my Friday workout".
#: No prescription — a replace inherits the source's.
_LIVE_REPLACE = {
    "day": "Cuma", "exercise": "Barbell Curl", "replacement": "Dumbbell Curl",
}

#: D. "Change Dumbbell Curl to 4x10". The model resolves the catalog name and
#: the only day carrying it.
_LIVE_UPDATE = {
    "day": "Cuma", "exercise": "Dumbbell Biceps Curl", "sets": 4, "reps": "10",
}

#: D2. The same sentence when the exercise sits on two days.
_LIVE_UPDATE_AMBIGUOUS = {
    "day": "Pazartesi", "exercise": "Dumbbell Biceps Curl",
    "sets": 4, "reps": "10",
}


# ── Fixtures ────────────────────────────────────────────────────────────────

def _program(monday, thursday, friday):
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Arms",
             "egzersizler": monday},
            {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Chest",
             "egzersizler": [{"isim": "Bench Press", "set": 4,
                              "tekrar": "8"}]},
            {"gun": "Perşembe", "tip": "antrenman", "odak": "Legs",
             "egzersizler": thursday},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Arms",
             "egzersizler": friday},
            {"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "egzersizler": []},
        ],
    }


_ARMS = [{"isim": "Barbell Curl", "set": 3, "tekrar": "10"},
         {"isim": "Triceps Pushdown", "set": 3, "tekrar": "12"}]
_LEGS = [{"isim": "Barbell Squat", "set": 4, "tekrar": "8"},
         {"isim": "Leg Press", "set": 3, "tekrar": "12"}]
_FRIDAY_ARMS = [{"isim": "Barbell Curl", "set": 4, "tekrar": "12"},
                {"isim": "Hammer Curl", "set": 3, "tekrar": "12"}]
_FRIDAY_CURLED = [{"isim": "Dumbbell Biceps Curl", "set": 4, "tekrar": "12"},
                  {"isim": "Hammer Curl", "set": 3, "tekrar": "12"}]


@pytest.fixture
def live_user(app, make_user):
    """One unique Legs day, Arms on two days — the shape the smoke ran against."""
    user = make_user("livepayload")
    seed_plan(user.id, _program(_ARMS, _LEGS, _FRIDAY_ARMS))
    return user


@pytest.fixture
def unique_curl_user(app, make_user):
    """Dumbbell Biceps Curl on exactly one day (D's unique-slot branch)."""
    user = make_user("liveuniquecurl")
    seed_plan(user.id, _program(_ARMS, _LEGS, _FRIDAY_CURLED))
    return user


@pytest.fixture
def ambiguous_curl_user(app, make_user):
    """Dumbbell Biceps Curl on two days (D's ambiguous branch)."""
    user = make_user("liveambiguouscurl")
    monday = [{"isim": "Dumbbell Biceps Curl", "set": 3, "tekrar": "10"},
              {"isim": "Triceps Pushdown", "set": 3, "tekrar": "12"}]
    seed_plan(user.id, _program(monday, _LEGS, _FRIDAY_CURLED))
    return user


def _turn(app, user_id, message, calls=(), language="en"):
    """One server Coach turn: begin, run the model's calls, settle the reply."""
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        ai_coach._begin_coach_turn(message, history=[], user_id=user_id)
        pending = coach_confirmation.resolve_pending_turn(user_id, language)
        if not calls:
            return [], pending
        payloads = [call(user_id, name, arguments) for name, arguments in calls]
        return payloads, coach_confirmation.reply_after_tools(
            user_id, language, payloads)


# ── Persistence helpers ─────────────────────────────────────────────────────

def _plan(user_id):
    db.session.expire_all()
    return (TrainingPlan.query.filter_by(user_id=user_id)
            .order_by(TrainingPlan.id.desc()).first())


def _day(user_id, day):
    document = json.loads(_plan(user_id).plan_data)
    for entry in document["program"]:
        if entry["gun"] == day:
            return [(e.get("isim"), e.get("set"), e.get("tekrar"))
                    for e in entry.get("egzersizler", [])]
    return None


def _snapshot(user_id):
    plan = _plan(user_id)
    return (
        plan.plan_data,
        plan.mutation_version,
        PlanMutationRecord.query.filter_by(user_id=user_id).count(),
        TrainingPlanConfirmationProposal.query.filter_by(
            user_id=user_id).count(),
        WorkoutLog.query.filter_by(user_id=user_id).count(),
        PendingAction.query.filter_by(user_id=user_id).count(),
    )


def _assert_no_side_effects(user_id, before):
    """A clarification turn moves NOTHING durable."""
    assert _snapshot(user_id) == before


def _assert_exactly_one_mutation(user_id, before):
    after = _snapshot(user_id)
    assert after[1] == before[1] + 1, "version must increment exactly once"
    assert after[2] == before[2] + 1, "exactly one journal effect"
    assert after[3] == before[3], "no confirmation proposal"
    assert after[4] == before[4], "no workout log"
    assert after[5] == before[5], "no pending action"


def _stored(user_id):
    return clarifications.load(user_id)


# ── The published contract the model reads ──────────────────────────────────

def _published(tool_name):
    for definition in schemas.PLAN_MUTATION_TOOL_DEFS:
        if definition["name"] == tool_name:
            return definition
    raise AssertionError(tool_name)


def test_the_model_is_never_asked_for_a_field_the_server_grounds():
    """The contract half of the incident, stated directly.

    The parser has accepted a partial ADD since PR #279. The schema kept
    demanding the same fields, so the model never sent one.
    """
    for tool, grounded in schemas.SERVER_GROUNDABLE.items():
        required = set(_published(tool)["parameters"].get("required", []))
        assert not (required & grounded), tool
    add = set(_published(schemas.ADD_EXERCISE_TOOL)
              ["parameters"].get("required", []))
    assert add == {"exercise"}
    update = set(_published(schemas.UPDATE_PRESCRIPTION_TOOL)
                 ["parameters"].get("required", []))
    assert update == {"exercise"}
    replace = set(_published(schemas.REPLACE_EXERCISE_TOOL)
                  ["parameters"].get("required", []))
    assert replace == {"exercise", "replacement"}


def test_the_add_description_no_longer_tells_the_model_to_ask_first():
    """"set ve tekrar ZORUNLUDUR — kullanıcı söylemediyse önce sor" is what the
    model obeyed. A description that contradicts the parser is a contract bug,
    not copy."""
    description = _published(schemas.ADD_EXERCISE_TOOL)["description"]
    assert "ZORUNLUDUR" not in description
    assert "HİÇ GÖNDERME" in description
    assert "aracı YİNE DE çağır" in description


def test_a_partial_add_payload_is_expressible_under_the_published_schema():
    """A's captured first call, checked against the contract that produced it.

    Built from the published ``required`` list rather than hardcoded, so
    restoring ``sets``/``reps`` to it fails here instead of silently making the
    payload below unreachable in production again.
    """
    required = set(_published(schemas.ADD_EXERCISE_TOOL)
                   ["parameters"].get("required", []))
    assert required <= set(_LIVE_ADD_PARTIAL)
    assert "reps" not in _LIVE_ADD_PARTIAL
    assert parser.parse_tool_arguments(ADD, _LIVE_ADD_PARTIAL) == {
        "day": "Perşembe", "exercise": "Walking Lunges", "sets": 4}


# ── A. partial ADD → the grounded half survives the clarification ───────────

def test_a_partial_add_keeps_the_sets_the_user_gave(app, live_user, tools_on):
    before = _snapshot(live_user.id)

    payloads, reply = _turn(
        app, live_user.id, "Add Walking Lunges with 4 sets to my leg workout",
        [(ADD, dict(_LIVE_ADD_PARTIAL))])

    assert payloads[0]["status"] == results.STATUS_NEEDS_INPUT
    assert payloads[0]["reason"] == results.REASON_MISSING_REPS
    assert "4 sets" in reply
    assert "sets and reps" not in reply

    record = _stored(live_user.id)
    assert record["operation"] == "add_exercise"
    assert record["sets"] == 4
    assert record["reps"] is None
    assert record["day"] == "Perşembe"
    assert record["exercise"] == "Walking Lunge"
    assert record["proposed_sets"] is None, "no 3x8-12 offer to accept by mistake"

    _assert_no_side_effects(live_user.id, before)


def test_a_bare_15_completes_the_partial_add_as_4x15(app, live_user, tools_on):
    _turn(app, live_user.id, "Add Walking Lunges with 4 sets to my leg workout",
          [(ADD, dict(_LIVE_ADD_PARTIAL))])
    before = _snapshot(live_user.id)

    # The continuation is SERVER-owned: no model call is scripted, because the
    # server short-circuits the provider loop for it.
    _payloads, reply = _turn(app, live_user.id, "15")

    assert "Walking Lunge" in reply
    assert _day(live_user.id, "Perşembe") == [
        ("Barbell Squat", 4, "8"), ("Leg Press", 3, "12"),
        ("Walking Lunge", 4, "15")]
    _assert_exactly_one_mutation(live_user.id, before)
    assert _day(live_user.id, "Pazartesi") == [
        ("Barbell Curl", 3, "10"), ("Triceps Pushdown", 3, "12")]

    # Consume-once, asserted behaviourally rather than by reading the store:
    # the record is taken, so repeating the continuation adds nothing.
    after_first = _snapshot(live_user.id)
    _turn(app, live_user.id, "15")
    _assert_no_side_effects(live_user.id, after_first)


def test_a_never_writes_the_models_own_three_sets(app, live_user, tools_on):
    """Invariant 1: the user said 4; a model-supplied 3 must not win.

    Same captured shape with the model also volunteering a prescription — the
    pre-fix model did exactly this once it was forced to fill ``reps``.
    """
    _turn(app, live_user.id, "Add Walking Lunges with 4 sets to my leg workout",
          [(ADD, dict(_LIVE_ADD_PARTIAL, sets=3, reps="12"))])
    record = _stored(live_user.id)
    assert record["sets"] == 4, "user-owned 4 sets, not the model's 3"
    assert record["reps"] is None, "the model's 12 is not a prescription"

    _turn(app, live_user.id, "15")
    assert ("Walking Lunge", 4, "15") in _day(live_user.id, "Perşembe")


# ── B. ambiguous ADD asks only the day and keeps 3x10 ───────────────────────

def test_b_ambiguous_add_asks_only_the_day(app, live_user, tools_on):
    before = _snapshot(live_user.id)

    payloads, reply = _turn(
        app, live_user.id, "Add Dumbbell Biceps Curl 3x10 to my arm workout",
        [(ADD, dict(_LIVE_ADD_AMBIGUOUS))])

    assert payloads[0]["status"] == results.STATUS_NEEDS_INPUT
    assert payloads[0]["reason"] == results.REASON_AMBIGUOUS_WORKOUT
    assert "Monday" in reply and "Friday" in reply
    assert "sets" not in reply.lower()

    record = _stored(live_user.id)
    assert record["operation"] == "add_exercise"
    assert record["exercise"] == "Dumbbell Biceps Curl"
    assert (record["sets"], record["reps"]) == (3, "10")
    assert record["day"] == "", "the model's guessed day is not kept"
    assert record["candidate_days"] == ["Pazartesi", "Cuma"]

    _assert_no_side_effects(live_user.id, before)


def test_b_monday_completes_the_exact_add(app, live_user, tools_on):
    _turn(app, live_user.id, "Add Dumbbell Biceps Curl 3x10 to my arm workout",
          [(ADD, dict(_LIVE_ADD_AMBIGUOUS))])
    before = _snapshot(live_user.id)

    _payloads, reply = _turn(app, live_user.id, "Monday")

    assert "Dumbbell Biceps Curl" in reply
    assert _day(live_user.id, "Pazartesi") == [
        ("Barbell Curl", 3, "10"), ("Triceps Pushdown", 3, "12"),
        ("Dumbbell Biceps Curl", 3, "10")]
    # An ADD, not a replace: Friday is untouched and Monday kept its own rows.
    assert _day(live_user.id, "Cuma") == [
        ("Barbell Curl", 4, "12"), ("Hammer Curl", 3, "12")]
    _assert_exactly_one_mutation(live_user.id, before)

    journal = (PlanMutationRecord.query.filter_by(user_id=live_user.id)
               .order_by(PlanMutationRecord.id).all())
    assert journal[-1].command_type == "add_exercise"


# ── C. explicit REPLACE needs no prescription ───────────────────────────────

def test_c_replace_executes_without_asking_for_sets_or_reps(
        app, live_user, tools_on):
    before = _snapshot(live_user.id)

    payloads, reply = _turn(
        app, live_user.id,
        "Replace Barbell Curl with Dumbbell Curl in my Friday workout",
        [(REPLACE, dict(_LIVE_REPLACE))])

    assert payloads[0]["status"] == results.STATUS_APPLIED
    assert "Dumbbell Biceps Curl" in reply
    # The source's own 4x12 is inherited, not re-asked and not defaulted.
    assert _day(live_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "12"), ("Hammer Curl", 3, "12")]
    assert _day(live_user.id, "Pazartesi") == [
        ("Barbell Curl", 3, "10"), ("Triceps Pushdown", 3, "12")]
    _assert_exactly_one_mutation(live_user.id, before)
    assert _stored(live_user.id) is None


# ── D. UPDATE resolves a unique slot, or asks only the day ──────────────────

def test_d_update_applies_directly_on_a_unique_slot(
        app, unique_curl_user, tools_on):
    before = _snapshot(unique_curl_user.id)

    payloads, _reply = _turn(
        app, unique_curl_user.id, "Change Dumbbell Curl to 4x10",
        [(PRESCRIBE, dict(_LIVE_UPDATE))])

    assert payloads[0]["status"] == results.STATUS_APPLIED
    assert _day(unique_curl_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "10"), ("Hammer Curl", 3, "12")]
    _assert_exactly_one_mutation(unique_curl_user.id, before)


def test_d_ambiguous_update_asks_only_the_day_and_keeps_4x10(
        app, ambiguous_curl_user, tools_on):
    before = _snapshot(ambiguous_curl_user.id)

    payloads, reply = _turn(
        app, ambiguous_curl_user.id, "Change Dumbbell Curl to 4x10",
        [(PRESCRIBE, dict(_LIVE_UPDATE_AMBIGUOUS))])

    assert payloads[0]["status"] == results.STATUS_NEEDS_INPUT
    assert payloads[0]["reason"] == results.REASON_AMBIGUOUS_WORKOUT
    assert "Monday" in reply and "Friday" in reply

    record = _stored(ambiguous_curl_user.id)
    assert record["operation"] == "update_exercise_prescription"
    assert (record["sets"], record["reps"]) == (4, "10")
    assert record["day"] == ""
    _assert_no_side_effects(ambiguous_curl_user.id, before)

    _turn(app, ambiguous_curl_user.id, "Friday")
    assert _day(ambiguous_curl_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "10"), ("Hammer Curl", 3, "12")]
    # Monday's own slot keeps its prescription — one day, one mutation.
    assert _day(ambiguous_curl_user.id, "Pazartesi") == [
        ("Dumbbell Biceps Curl", 3, "10"), ("Triceps Pushdown", 3, "12")]
    _assert_exactly_one_mutation(ambiguous_curl_user.id, before)


# ── The output guard: a question is not a claim ─────────────────────────────

@pytest.mark.parametrize("provider_text", [
    "You have arm work on Monday and Friday. Which day would you like me to "
    "add the Dumbbell Biceps Curl to?",
    "I found Barbell Curl on both Monday and Friday. Which one should I "
    "replace with Dumbbell Curl?",
    "I see Dumbbell Curl on Monday and Friday. Which day should I update?",
    "How many reps would you like for the Walking Lunges?",
    "What day is your arm workout? Shall I add Dumbbell Biceps Curl 3x10?",
    "Hangi güne eklememi istersin?",
    "Kaç set istersin? Programa ekleyeyim mi?",
])
def test_a_request_for_a_missing_discriminator_reaches_the_user(
        app, live_user, provider_text):
    """B/C/D's live symptom: the question was replaced by "please repeat…".

    ``grounded_provider_reply`` exists to stop the model claiming a pending
    proposal exists. Asking which day claims nothing.
    """
    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            live_user.id, "en", provider_text)

    assert reply == provider_text


@pytest.mark.parametrize("provider_text", [
    "Shall I replace Barbell Curl with Dumbbell Curl on Friday?",
    "Would you like me to change that exercise to 4 sets of 10?",
    "I've prepared the change. Reply yes to confirm and I'll remove Bench "
    "Press from your plan.",
    # The exemption is for questions that ask for a field, not for any
    # sentence that happens to contain "what" or a Turkish word starting
    # "kaç" — a broader pattern would retire the guard instead of fixing it.
    "What a great week! Shall I add Lateral Raise to your Monday plan?",
    "Antrenmanı kaçırdın. Programa ekleyeyim mi?",
])
def test_a_bare_confirmation_invite_is_still_suppressed(
        app, live_user, provider_text):
    """The guard's own job is untouched: no durable proposal, no such copy."""
    with app.test_request_context("/ask", method="POST"):
        reply = coach_confirmation.grounded_provider_reply(
            live_user.id, "en", provider_text)

    assert reply != provider_text
    assert "nothing was changed" in reply.lower()


# ── Phase 5: the same payload through /ask/stream, adapter included ─────────

def test_ask_stream_partial_add_then_15_writes_4x15(
        app, live_user, tools_on, monkeypatch):
    """End to end: provider adapter → parser → grounding → clarification →
    continuation → persisted plan. The provider boundary is scripted with the
    captured tool-call SHAPE; nothing below it is mocked."""
    from app.services import ai_stream
    from tests.test_ai_stream import (
        _FakeBedrock, _FakeStream, _final, _tool_use_block,
    )

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(
        ai_coach, "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("OpenAI fallback must not run"))
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([
        _FakeStream(
            [],
            _final(
                stop_reason="tool_use",
                content=[_tool_use_block(
                    ADD, "t1", dict(_LIVE_ADD_PARTIAL))],
            ),
        ),
    ]))

    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        events = list(ai_stream.stream_coach_answer(
            live_user.id, "Add Walking Lunges with 4 sets to my leg workout",
            "", [], language="en"))

    visible = "".join(
        e.get("text") or "" for e in events if e.get("type") == "delta")
    assert "4 sets" in visible
    assert _stored(live_user.id)["sets"] == 4
    before = _snapshot(live_user.id)

    # Second turn: the server owns it, so the provider is never reached.
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([]))
    _payloads, reply = _turn(app, live_user.id, "15")

    assert "Walking Lunge" in reply
    assert ("Walking Lunge", 4, "15") in _day(live_user.id, "Perşembe")
    _assert_exactly_one_mutation(live_user.id, before)


def _script_one_tool_call(monkeypatch, tool, arguments):
    """Script one Bedrock turn that emits exactly the captured tool call."""
    from tests.test_ai_stream import (
        _FakeBedrock, _FakeStream, _final, _tool_use_block,
    )

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(
        ai_coach, "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("OpenAI fallback must not run"))
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([
        _FakeStream(
            [],
            _final(
                stop_reason="tool_use",
                content=[_tool_use_block(tool, "t1", dict(arguments))],
            ),
        ),
    ]))


def _stream(app, user_id, question):
    from app.services import ai_stream

    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        events = list(ai_stream.stream_coach_answer(
            user_id, question, "", [], language="en"))
    return "".join(
        e.get("text") or "" for e in events if e.get("type") == "delta")


def test_ask_stream_replace_applies_without_a_prescription(
        app, live_user, tools_on, monkeypatch):
    """C at the adapter boundary: no sets/reps asked for, source rx inherited."""
    before = _snapshot(live_user.id)
    _script_one_tool_call(monkeypatch, REPLACE, _LIVE_REPLACE)

    visible = _stream(
        app, live_user.id,
        "Replace Barbell Curl with Dumbbell Curl in my Friday workout")

    assert "Dumbbell Biceps Curl" in visible
    assert "sets and reps" not in visible
    assert _day(live_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "12"), ("Hammer Curl", 3, "12")]
    _assert_exactly_one_mutation(live_user.id, before)


def test_ask_stream_update_applies_on_a_unique_slot(
        app, unique_curl_user, tools_on, monkeypatch):
    """D at the adapter boundary: unique slot, direct 4x10."""
    before = _snapshot(unique_curl_user.id)
    _script_one_tool_call(monkeypatch, PRESCRIBE, _LIVE_UPDATE)

    _stream(app, unique_curl_user.id, "Change Dumbbell Curl to 4x10")

    assert _day(unique_curl_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "10"), ("Hammer Curl", 3, "12")]
    _assert_exactly_one_mutation(unique_curl_user.id, before)


def test_ask_stream_ambiguous_update_asks_only_the_day(
        app, ambiguous_curl_user, tools_on, monkeypatch):
    """D's ambiguous branch at the adapter boundary: the day question survives
    and 4x10 is held for the continuation."""
    before = _snapshot(ambiguous_curl_user.id)
    _script_one_tool_call(monkeypatch, PRESCRIBE, _LIVE_UPDATE_AMBIGUOUS)

    visible = _stream(
        app, ambiguous_curl_user.id, "Change Dumbbell Curl to 4x10")

    assert "Monday" in visible and "Friday" in visible
    assert "repeat the exercise" not in visible
    _assert_no_side_effects(ambiguous_curl_user.id, before)
    record = _stored(ambiguous_curl_user.id)
    assert (record["sets"], record["reps"]) == (4, "10")

    _turn(app, ambiguous_curl_user.id, "Friday")
    assert _day(ambiguous_curl_user.id, "Cuma") == [
        ("Dumbbell Biceps Curl", 4, "10"), ("Hammer Curl", 3, "12")]
    _assert_exactly_one_mutation(ambiguous_curl_user.id, before)


def test_ask_stream_ambiguous_add_question_is_not_replaced(
        app, live_user, tools_on, monkeypatch):
    """The live B/C/D symptom, at the layer that produced it.

    The model ends its turn with a disambiguation question and no tool call —
    exactly what production did — and the user must see that question rather
    than "please repeat the exercise, day, sets, and reps".
    """
    from app.services import ai_stream
    from tests.test_ai_stream import (
        _FakeBedrock, _FakeStream, _final, _text_block,
    )

    question = ("Your arm workout is on Monday and Friday. Which day should I "
                "add the Dumbbell Biceps Curl to?")
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(
        ai_coach, "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("OpenAI fallback must not run"))
    monkeypatch.setattr(ai_coach, "bedrock_client", _FakeBedrock([
        _FakeStream([question], _final(content=[_text_block(question)])),
    ]))
    before = _snapshot(live_user.id)

    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        events = list(ai_stream.stream_coach_answer(
            live_user.id, "Add Dumbbell Biceps Curl 3x10 to my arm workout",
            "", [], language="en"))

    visible = "".join(
        e.get("text") or "" for e in events if e.get("type") == "delta")
    assert question in visible
    assert "repeat the exercise" not in visible
    _assert_no_side_effects(live_user.id, before)
