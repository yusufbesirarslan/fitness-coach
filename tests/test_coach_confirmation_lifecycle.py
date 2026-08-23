"""Server-owned confirmation lifecycle: pending actions execute once.

The Coach used to send "yes" / "proceed" through the LLM tool loop. The model
could restage a workout log (or stage one for a plan mutation) instead of
consuming the canonical pending action, which restarted confirmation.

These tests drive the real pending stores and persistence. Provider loops are
scripted only where the test must prove the LLM is skipped or cannot restage.

    python -m pytest tests/test_coach_confirmation_lifecycle.py tests/test_plan_confirmation_parser.py -v
"""
import json

import pytest

from app.extensions import db
from app.models import PendingAction, WorkoutLog, WorkoutSession
from app.observability import assign_request_id
from app.services import ai_coach, coach_plan_tools, plan_confirmation
from app.services.coach_plan_tools import results
from app.services.workout_session.models import compute_fingerprint
from app.timeutil import day_key
from tests.test_ai_coach import _ScriptedLLM, _llm_msg, _tool_call
from tests.test_coach_plan_tool_characterization import (  # noqa: F401
    _program, planned_user,
)
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, REMOVE, call, names, plan_version, tools_on,
)


def _new_turn(app, message=""):
    assign_request_id()
    ai_coach._begin_coach_turn(message)


def _stage_workout(user, exercise="Lateral Raise", sets=3, reps=10, weight=0):
    return json.loads(ai_coach._tool_stage_workout_log(
        user.id, exercise, sets, reps, weight))


def _pending_logs(user):
    return PendingAction.query.filter_by(user_id=user.id).all()


def _workout_logs(user):
    return WorkoutLog.query.filter_by(user_id=user.id).all()


def _tool_names(user):
    return [t["name"] for t in ai_coach._coach_tool_defs_for_call(user.id)]


# ---------------------------------------------------------------------------
# Workout log confirmation
# ---------------------------------------------------------------------------

def test_workout_log_proceed_executes_once_and_clears_pending(
        app, auth_user, monkeypatch):
    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call(
            "stage_workout_log",
            '{"exercise_name": "Lateral Raise", "sets": 3, "reps": 10}')]),
        _llm_msg("Would you like to confirm this workout log?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)

    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        staged = _stage_workout(auth_user)
    assert staged["status"] == "staged"
    assert _pending_logs(auth_user)

    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            auth_user.id, "proceed", "", client_history=[], language="en")

    logs = _workout_logs(auth_user)
    assert len(logs) == 1
    assert logs[0].exercise_name == "Lateral Raise"
    assert logs[0].sets == 3
    assert logs[0].reps == 10
    assert _pending_logs(auth_user) == []
    assert llm.calls == []
    assert "logged" in reply.lower()
    assert "confirm" not in reply.lower()
    assert "would you like" not in reply.lower()


def test_workout_log_yes_executes_once(app, auth_user, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user, "Bench Press", 5, 5, 100)
    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            auth_user.id, "yes", "", client_history=[], language="en")
    logs = _workout_logs(auth_user)
    assert len(logs) == 1
    assert logs[0].exercise_name == "Bench Press"
    assert logs[0].volume == 2500.0
    assert _pending_logs(auth_user) == []
    assert "logged" in reply.lower()


def test_workout_log_no_cancels_without_persistence(
        app, auth_user, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            auth_user.id, "no", "", client_history=[], language="en")
    assert _workout_logs(auth_user) == []
    assert _pending_logs(auth_user) == []
    assert "cancel" in reply.lower()


def test_ambiguous_reply_does_not_execute_or_restage(
        app, auth_user, monkeypatch):
    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call(
            "stage_workout_log",
            '{"exercise_name": "Lateral Raise", "sets": 3, "reps": 10}')]),
        _llm_msg("Would you like to confirm this workout log?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            auth_user.id, "what about my calories?", "",
            client_history=[], language="en")
    assert _workout_logs(auth_user) == []
    pending = _pending_logs(auth_user)
    assert len(pending) == 1
    assert pending[0].payload["exercise_name"] == "Lateral Raise"
    assert llm.calls == []
    assert "yes" in reply.lower() and "no" in reply.lower()


def test_duplicate_proceed_does_not_log_twice(app, auth_user, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    restage = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call(
            "stage_workout_log",
            '{"exercise_name": "Lateral Raise", "sets": 3, "reps": 10}')]),
        _llm_msg(tool_calls=[_tool_call("confirm_and_commit_workout_log")]),
        _llm_msg("logged again"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", restage)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    with app.test_request_context("/ask", method="POST"):
        ai_coach._run_coach_conversation(
            auth_user.id, "proceed", "", client_history=[], language="en")
    assert len(_workout_logs(auth_user)) == 1
    with app.test_request_context("/ask", method="POST"):
        ai_coach._run_coach_conversation(
            auth_user.id, "proceed", "", client_history=[], language="en")
    assert len(_workout_logs(auth_user)) == 1
    assert _pending_logs(auth_user) == []


def test_standalone_yes_does_not_execute_stale_or_new_log(
        app, auth_user, monkeypatch):
    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call(
            "stage_workout_log",
            '{"exercise_name": "Ghost Lift", "sets": 3, "reps": 10}')]),
        _llm_msg("Would you like to confirm this workout log?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            auth_user.id, "yes", "", client_history=[], language="en")
    assert _workout_logs(auth_user) == []
    assert _pending_logs(auth_user) == []
    if llm.calls:
        names = []
        for call in llm.calls:
            names.extend(
                t["function"]["name"] for t in call.get("tools") or [])
        assert "stage_workout_log" not in names
        assert "fetch_nutrition_and_stage_log" not in names


# ---------------------------------------------------------------------------
# Training-plan mutation confirmation
# ---------------------------------------------------------------------------

def test_plan_mutation_yes_applies_once(app, planned_user, tools_on, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        staged = call(planned_user.id, REMOVE,
                      {"day": "Pazartesi", "exercise": "Bench Press"})
    assert staged["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert plan_confirmation.get_pending(planned_user.id) is not None
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]

    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            planned_user.id, "yes", "", client_history=[], language="en")

    assert names(planned_user.id) == ["Shoulder Press"]
    assert plan_version(planned_user.id) == 1
    assert plan_confirmation.get_pending(planned_user.id) is None
    assert "confirm" not in reply.lower()
    assert "bench press" in reply.lower()


def test_plan_mutation_proceed_applies_once(
        app, planned_user, tools_on, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            planned_user.id, "proceed", "", client_history=[], language="en")
    assert names(planned_user.id) == ["Shoulder Press"]
    assert plan_confirmation.get_pending(planned_user.id) is None
    assert "bench press" in reply.lower()


def test_plan_add_with_session_impact_yes_adds_once(
        app, planned_user, tools_on, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    from app.services.today_facts import get_active_plan
    plan = get_active_plan(planned_user.id)
    db.session.add(WorkoutSession(
        user_id=planned_user.id, status="active", workout_date=day_key(),
        weekday_slot="Pazartesi", source="scheduled",
        planned_training_plan_id=plan.id,
        plan_fingerprint=compute_fingerprint(["Bench Press", "Shoulder Press"])))
    db.session.commit()

    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        staged = call(planned_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Lateral Raise",
            "sets": 3, "reps": "10",
        })
    assert staged["status"] == results.STATUS_CONFIRMATION_REQUIRED
    assert "Lateral Raise" not in names(planned_user.id)

    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            planned_user.id, "yes", "", client_history=[], language="en")

    assert "Lateral Raise" in names(planned_user.id)
    assert plan_confirmation.get_pending(planned_user.id) is None
    assert _workout_logs(planned_user) == []
    assert "lateral raise" in reply.lower()
    assert "logged" not in reply.lower()


def test_plan_pending_yes_does_not_stage_a_workout_log(
        app, planned_user, tools_on, monkeypatch):
    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[_tool_call(
            "stage_workout_log",
            '{"exercise_name": "Lateral Raise", "sets": 3, "reps": 10}')]),
        _llm_msg("Would you like to confirm this workout log?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        ai_coach._run_coach_conversation(
            planned_user.id, "yes", "", client_history=[], language="en")
    assert names(planned_user.id) == ["Shoulder Press"]
    assert _workout_logs(planned_user) == []
    assert _pending_logs(planned_user) == []
    assert llm.calls == []


def test_cross_action_isolation_does_not_resolve_the_wrong_pending(
        app, planned_user, tools_on, monkeypatch):
    monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([]))
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(planned_user, "Lateral Raise", 3, 10, 0)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    assert plan_confirmation.get_pending(planned_user.id) is not None
    assert _pending_logs(planned_user)

    with app.test_request_context("/ask", method="POST"):
        reply = ai_coach._run_coach_conversation(
            planned_user.id, "yes", "", client_history=[], language="en")

    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert _workout_logs(planned_user) == []
    assert plan_confirmation.get_pending(planned_user.id) is not None
    assert _pending_logs(planned_user)
    assert "yes" in reply.lower() and "no" in reply.lower()


def test_retry_of_workout_confirm_is_idempotent(app, auth_user):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "proceed")
        first = json.loads(ai_coach._tool_confirm_and_commit_workout_log(
            auth_user.id))
        second = json.loads(ai_coach._tool_confirm_and_commit_workout_log(
            auth_user.id))
    assert first["status"] == "committed"
    assert second["status"] == "no_pending"
    assert len(_workout_logs(auth_user)) == 1


def test_stage_workout_refused_while_plan_confirmation_pending(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        call(planned_user.id, REMOVE,
             {"day": "Pazartesi", "exercise": "Bench Press"})
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "yes")
        result = json.loads(ai_coach._tool_stage_workout_log(
            planned_user.id, "Lateral Raise", 3, 10, 0))
    assert result["status"] != "staged"
    assert _pending_logs(planned_user) == []
    assert plan_confirmation.get_pending(planned_user.id) is not None


def test_confirm_intent_hides_staging_tools(app, auth_user):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "proceed")
        published = _tool_names(auth_user)
    assert "stage_workout_log" not in published
    assert "fetch_nutrition_and_stage_log" not in published


def test_canonical_workout_stage_reply_does_not_claim_persistence(
        app, auth_user):
    from app.services import coach_confirmation
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user, "Lateral Raise", 3, 10, 0)
        reply = coach_confirmation.canonical_pending_prompt(
            auth_user.id, "en")
    assert reply is not None
    assert "lateral raise" in reply.lower()
    assert "i've added" not in reply.lower()
    assert "i have added" not in reply.lower()
    assert _workout_logs(auth_user) == []


def _visible_text(events):
    return "".join(e.get("text") or "" for e in events if e.get("type") == "delta")


def _assert_no_premature_success(text):
    lowered = text.lower()
    for phrase in ("i've added", "i have added", "i've logged", "i have logged",
                   "done —", "done -", "your workout has been updated"):
        assert phrase not in lowered, text


def _script_bedrock_stream(monkeypatch, turns):
    from tests.test_ai_stream import _FakeBedrock

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    fake = _FakeBedrock(turns)
    monkeypatch.setattr(ai_coach, "bedrock_client", fake)
    monkeypatch.setattr(
        ai_coach, "_run_coach_conversation_openai",
        lambda *a, **k: pytest.fail("OpenAI fallback must not run"))
    return fake


def test_streaming_proceed_skips_provider(app, auth_user, monkeypatch):
    from app.services import ai_stream

    llm = _ScriptedLLM([
        _llm_msg("Would you like to confirm this workout log?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        _stage_workout(auth_user)
    events = []
    with app.test_request_context("/ask/stream", method="POST"):
        for event in ai_stream.stream_coach_answer(
                auth_user.id, "proceed", "", [], language="en"):
            events.append(event)
    done = [e for e in events if e.get("type") == "done"]
    assert done
    assert "logged" in done[-1]["text"].lower()
    assert len(_workout_logs(auth_user)) == 1
    assert llm.calls == []


def test_streaming_confirmation_proposal_does_not_emit_premature_success(
        app, planned_user, tools_on, monkeypatch):
    from app.observability import assign_request_id
    from app.services import ai_stream
    from tests.test_ai_stream import (
        _FakeStream, _final, _text_block, _tool_use_block,
    )

    premature = "I've added Lateral Raise to your workout."
    _script_bedrock_stream(monkeypatch, [
        _FakeStream(
            [premature],
            _final(
                stop_reason="tool_use",
                content=[
                    _text_block(premature),
                    _tool_use_block(
                        "remove_training_plan_exercise", "t1",
                        {"day": "Pazartesi", "exercise": "Bench Press"}),
                ],
            ),
        ),
        _FakeStream(
            ["Done — Bench Press has been removed from Pazartesi."],
            _final(content=[_text_block(
                "Done — Bench Press has been removed from Pazartesi.")])),
    ])

    events = []
    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        for event in ai_stream.stream_coach_answer(
                planned_user.id, "remove bench press from monday",
                "", [], language="en"):
            events.append(event)

    visible = _visible_text(events)
    _assert_no_premature_success(visible)
    done = [e for e in events if e.get("type") == "done"]
    assert done
    assert "confirm" in done[-1]["text"].lower()
    assert "bench press" in done[-1]["text"].lower()
    assert names(planned_user.id) == ["Bench Press", "Shoulder Press"]
    assert plan_confirmation.get_pending(planned_user.id) is not None
    assert _workout_logs(planned_user) == []


def test_streaming_workout_stage_does_not_claim_logged(
        app, auth_user, monkeypatch):
    from app.observability import assign_request_id
    from app.services import ai_stream
    from tests.test_ai_stream import (
        _FakeStream, _final, _text_block, _tool_use_block,
    )

    premature = "I've logged Lateral Raise. Done."
    _script_bedrock_stream(monkeypatch, [
        _FakeStream(
            [premature],
            _final(
                stop_reason="tool_use",
                content=[
                    _text_block(premature),
                    _tool_use_block(
                        "stage_workout_log", "t1",
                        {"exercise_name": "Lateral Raise",
                         "sets": 3, "reps": 10}),
                ],
            ),
        ),
        _FakeStream(
            ["Logged."],
            _final(content=[_text_block("Logged.")])),
    ])

    events = []
    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        for event in ai_stream.stream_coach_answer(
                auth_user.id, "log 3x10 lateral raise",
                "", [], language="en"):
            events.append(event)

    visible = _visible_text(events)
    _assert_no_premature_success(visible)
    assert "i've logged" not in visible.lower()
    assert "logged." not in visible.lower()
    done = [e for e in events if e.get("type") == "done"]
    assert done
    assert "log this workout" in done[-1]["text"].lower()
    assert _workout_logs(auth_user) == []
    assert _pending_logs(auth_user)


def test_apply_now_add_without_session_persists_immediately(
        app, planned_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app, "add lateral raise to monday")
        result = call(planned_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Lateral Raise",
            "sets": 3, "reps": "10",
        })
        from app.services import coach_confirmation
        reply = coach_confirmation.reply_after_tools(
            planned_user.id, "en", [result])

    assert result["status"] == results.STATUS_APPLIED
    assert "Lateral Raise" in names(planned_user.id)
    assert plan_confirmation.get_pending(planned_user.id) is None
    assert _workout_logs(planned_user) == []
    assert "lateral raise" in reply.lower()
    assert "added" in reply.lower()
    assert "confirm" not in reply.lower()


def test_streaming_apply_now_does_not_emit_success_before_persist(
        app, planned_user, tools_on, monkeypatch):
    from app.observability import assign_request_id
    from app.services import ai_stream
    from tests.test_ai_stream import (
        _FakeStream, _final, _text_block, _tool_use_block,
    )

    premature = "I've added Lateral Raise to your workout."
    fake = _script_bedrock_stream(monkeypatch, [
        _FakeStream(
            [premature],
            _final(
                stop_reason="tool_use",
                content=[
                    _text_block(premature),
                    _tool_use_block(
                        "add_training_plan_exercise", "t1",
                        {"day": "Pazartesi", "exercise": "Lateral Raise",
                         "sets": 3, "reps": "10"}),
                ],
            ),
        ),
        _FakeStream(
            ["Please confirm the add."],
            _final(content=[_text_block("Please confirm the add.")])),
    ])

    events = []
    with app.test_request_context("/ask/stream", method="POST"):
        assign_request_id()
        for event in ai_stream.stream_coach_answer(
                planned_user.id, "add lateral raise to my chest day",
                "", [], language="en"):
            events.append(event)

    visible = _visible_text(events)
    assert premature.lower() not in visible.lower()
    assert "please confirm" not in visible.lower()
    done = [e for e in events if e.get("type") == "done"]
    assert done
    assert "lateral raise" in done[-1]["text"].lower()
    assert "added" in done[-1]["text"].lower()
    assert "confirm" not in done[-1]["text"].lower()
    assert "Lateral Raise" in names(planned_user.id)
    assert plan_confirmation.get_pending(planned_user.id) is None
    assert _workout_logs(planned_user) == []
    assert len(fake.calls) == 1


def test_apply_now_later_yes_does_not_mutate_or_stage_workout(
        app, planned_user, tools_on, monkeypatch):
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    with app.test_request_context("/ask", method="POST"):
        _new_turn(app)
        result = call(planned_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Lateral Raise",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_APPLIED
    assert plan_confirmation.get_pending(planned_user.id) is None

    llm = _ScriptedLLM([
        _llm_msg(tool_calls=[
            _tool_call(
                "add_training_plan_exercise",
                json.dumps({
                    "day": "Pazartesi", "exercise": "Ghost Raise",
                    "sets": 3, "reps": "10",
                })),
            _tool_call(
                "stage_workout_log",
                '{"exercise_name": "Lateral Raise", "sets": 3, "reps": 10}'),
        ]),
        _llm_msg("I've added Ghost Raise. Confirm?"),
    ])
    monkeypatch.setattr(ai_coach, "openai_client", llm)
    with app.test_request_context("/ask", method="POST"):
        ai_coach._run_coach_conversation(
            planned_user.id, "yes", "", client_history=[], language="en")

    assert names(planned_user.id).count("Lateral Raise") == 1
    assert "Ghost Raise" not in names(planned_user.id)
    assert _workout_logs(planned_user) == []
    assert _pending_logs(planned_user) == []
    assert plan_confirmation.get_pending(planned_user.id) is None
    if llm.calls:
        published = []
        for item in llm.calls:
            published.extend(
                t["function"]["name"] for t in item.get("tools") or [])
        assert "add_training_plan_exercise" not in published
        assert "stage_workout_log" not in published
