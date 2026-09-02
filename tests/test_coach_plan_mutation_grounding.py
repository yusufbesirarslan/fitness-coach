"""Coach-owned grounding of plan-mutation targets and prescriptions.

Drives the real executor, confirmation copy, and (where needed) the Coach
route with a scripted provider. Persistence assertions are on the plan
row, the journal, and confirmation proposals — not on mock call counts.
"""
import json

import pytest

from app.extensions import db
from app.models import (
    PlanMutationRecord,
    TrainingPlan,
    TrainingPlanConfirmationProposal,
    WorkoutLog,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_confirmation, coach_plan_tools, plan_confirmation
from app.services.coach_plan_tools import results
from app.services.coach_plan_tools.exercise_grounding import resolve_destination
from app.services.coach_plan_tools.weekdays import (
    ENGLISH_WEEKDAYS,
    canonicalize_weekday,
    localize_weekday,
)
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, call, journal, names, plan_version, seed_plan, tools_on, turn,
)
from tests.test_coach_plan_tool_characterization import _program  # noqa: F401


def _split_program():
    """Monday chest, Wednesday back, Friday legs — the spec fixture."""
    return {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman",
             "odak": "Chest / Triceps", "egzersizler": [
                 {"isim": "Push-Up", "set": 3, "tekrar": "10"}]},
            {"gun": "Salı", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Çarşamba", "tip": "antrenman",
             "odak": "Back / Biceps", "egzersizler": [
                 {"isim": "Dumbbell Row", "set": 3, "tekrar": "8"}]},
            {"gun": "Perşembe", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Cuma", "tip": "antrenman", "odak": "Legs",
             "egzersizler": [
                 {"isim": "Bodyweight Squat", "set": 3, "tekrar": "12"}]},
            {"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []},
            {"gun": "Pazar", "tip": "dinlenme", "egzersizler": []},
        ],
        "exercise_context": {
            "equipment_context": "minimal", "cardio_type": "yok",
            "style": "general_fitness", "catalog_version": 1,
        },
    }


def _ambiguous_legs_program():
    program = _split_program()
    program["program"][2]["odak"] = "Lower Body"
    program["program"][2]["egzersizler"] = [
        {"isim": "Bodyweight Squat", "set": 3, "tekrar": "10"}]
    return program


@pytest.fixture
def split_user(app, make_user):
    user = make_user("groundedcoach")
    seed_plan(user.id, _split_program())
    return user


def _fresh_turn(app, message, history=None):
    assign_request_id()
    ai_coach._begin_coach_turn(message, history=history or [])


def _snapshot(user_id):
    from app.services.today_facts import get_active_plan
    db.session.expire_all()
    plan = get_active_plan(user_id)
    return (
        plan.plan_data,
        plan.mutation_version,
        PlanMutationRecord.query.filter_by(user_id=user_id).count(),
        TrainingPlanConfirmationProposal.query.filter_by(
            user_id=user_id).count(),
        WorkoutLog.query.filter_by(user_id=user_id).count(),
    )


def _assert_unchanged(user_id, before):
    assert _snapshot(user_id) == before


# ── Helpers ─────────────────────────────────────────────────────────────────

def test_english_weekdays_match_training_js_presentation():
    from pathlib import Path
    js = Path("static/training.js").read_text(encoding="utf-8")
    for turkish, english in zip(
            __import__(
                "app.services.plan_mutation.validation",
                fromlist=["WEEKDAYS"]).WEEKDAYS,
            ENGLISH_WEEKDAYS):
        assert f"'{turkish}':'{english}'" in js
        assert localize_weekday(turkish, "en") == english
        assert localize_weekday(turkish, "tr") == turkish
        assert canonicalize_weekday(english) == turkish
        assert canonicalize_weekday(turkish) == turkish


def test_walking_lunges_resolves_to_catalog_canonical():
    target = resolve_destination("Walking Lunges")
    assert target.kind == "resolved"
    assert target.canonical_name == "Walking Lunge"


def test_squats_is_a_suggestion_not_a_silent_rewrite():
    target = resolve_destination("squats")
    assert target.kind == "suggest"
    assert "Squat" in target.suggestion


def test_blabla_is_unknown():
    target = resolve_destination("blabla")
    assert target.kind == "unknown"


def test_dambil_curl_suggests_a_curl():
    target = resolve_destination("dambıl curl")
    assert target.kind == "suggest"
    assert "Curl" in target.suggestion


# ── Acceptance matrix ───────────────────────────────────────────────────────

def test_add_to_leg_workout_without_rx_does_not_mutate(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "en", [result])

    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_MISSING_PRESCRIPTION
    assert result["change"]["day"] == "Cuma"
    assert result["change"]["exercise"] == "Walking Lunge"
    _assert_unchanged(split_user.id, before)
    assert "friday" in reply.lower()
    assert "walking lunge" in reply.lower()
    assert "how many" in reply.lower()
    assert plan_confirmation.get_pending(split_user.id) is None


def test_add_walking_lunges_3x12_to_leg_workout_mutates_once(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges 3x12 to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "en", [result])

    assert result["status"] == results.STATUS_APPLIED
    assert names(split_user.id, "Cuma")[-1] == "Walking Lunge"
    from app.services.today_facts import get_active_plan
    document = json.loads(get_active_plan(split_user.id).plan_data)
    friday = next(d for d in document["program"] if d["gun"] == "Cuma")
    added = friday["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert added["exercise_id"] == "ex_walking_lunge"
    assert added["set"] == 3
    assert added["tekrar"] == "12"
    monday = next(d for d in document["program"] if d["gun"] == "Pazartesi")
    assert [e["isim"] for e in monday["egzersizler"]] == ["Push-Up"]
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1
    assert "friday" in reply.lower()
    assert WorkoutLog.query.filter_by(user_id=split_user.id).count() == 0


def test_add_to_explicit_friday_resolves_canonical_slot(
        app, split_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges 3x12 to Friday.")
        result = call(split_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_APPLIED
    assert names(split_user.id, "Cuma")[-1] == "Walking Lunge"
    assert names(split_user.id, "Pazartesi") == ["Push-Up"]


def test_unknown_exercise_does_not_create_a_proposal(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add blabla 3x10 to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "blabla",
            "sets": 3, "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "en", [result])

    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_EXERCISE_UNKNOWN
    _assert_unchanged(split_user.id, before)
    assert plan_confirmation.get_pending(split_user.id) is None
    assert "couldn't find" in reply.lower()


def test_dambil_curl_suggests_and_does_not_mutate(app, make_user, tools_on):
    user = make_user("armday")
    program = _split_program()
    program["program"][0]["odak"] = "Chest"
    program["program"][2]["odak"] = "Arms"
    seed_plan(user.id, program)
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add dambıl curl 3x10 to my arm workout.")
        result = call(user.id, ADD, {
            "day": "Pazartesi", "exercise": "dambıl curl",
            "sets": 3, "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(
            user.id, "en", [result])

    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_EXERCISE_SUGGEST
    _assert_unchanged(user.id, before)
    assert "did you mean" in reply.lower()
    assert "curl" in reply.lower()


def test_squats_suggests_without_auto_substitution(app, split_user, tools_on):
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add squats 3x10 to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "squats",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_EXERCISE_SUGGEST
    _assert_unchanged(split_user.id, before)


def test_ambiguous_leg_workouts_clarify(app, make_user, tools_on):
    user = make_user("twolegs")
    seed_plan(user.id, _ambiguous_legs_program())
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges 3x12 to my leg workout.")
        result = call(user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunges",
            "sets": 3, "reps": "12",
        })
        reply = coach_confirmation.reply_after_tools(user.id, "en", [result])
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_AMBIGUOUS_WORKOUT
    _assert_unchanged(user.id, before)
    assert "more than one" in reply.lower()


def test_english_copy_localizes_cuma_to_friday(app, split_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges 3x12 to Friday.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunge",
            "sets": 3, "reps": "12",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "en", [result])
    assert "friday" in reply.lower()
    assert "cuma" not in reply.lower()


def test_turkish_copy_keeps_cuma(app, split_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Cuma gününe Walking Lunges 3x12 ekle.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunge",
            "sets": 3, "reps": "12",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "tr", [result])
    assert "Cuma" in reply
    assert "Friday" not in reply


def test_sets_without_reps_does_not_fabricate_reps(app, split_user, tools_on):
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges with 4 sets to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_MISSING_REPS
    _assert_unchanged(split_user.id, before)


def test_accepting_proposed_prescription_applies_only_those_numbers(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    history = [
        {"role": "user",
         "content": "Add Walking Lunges to my leg workout."},
        {"role": "assistant",
         "content": ("I found your lower body workout on Friday. "
                     "How many sets and reps should I add for Walking Lunge? "
                     "Would you like 3 sets of 8-12 reps?")},
    ]
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "yes", history=history)
        reply = coach_confirmation.resolve_pending_turn(split_user.id, "en")

    assert reply is not None
    assert names(split_user.id, "Cuma")[-1] == "Walking Lunge"
    from app.services.today_facts import get_active_plan
    document = json.loads(get_active_plan(split_user.id).plan_data)
    added = next(d for d in document["program"]
                 if d["gun"] == "Cuma")["egzersizler"][-1]
    assert added["set"] == 3
    assert added["tekrar"] == "8-12"
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1


def test_route_add_without_rx_asks_and_does_not_mutate(
        app, split_user, tools_on, monkeypatch):
    from tests.test_ai_coach import _ScriptedLLM, _llm_msg, _tool_call

    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    before = _snapshot(split_user.id)
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([
            _llm_msg(tool_calls=[_tool_call(
                "add_training_plan_exercise",
                json.dumps({
                    "day": "Pazartesi", "exercise": "Walking Lunges",
                    "sets": 3, "reps": "10",
                }))]),
            _llm_msg("I added them."),
        ]))
        reply = ai_coach._run_coach_conversation(
            split_user.id, "Add Walking Lunges to my leg workout.",
            "", client_history=[], language="en")

    _assert_unchanged(split_user.id, before)
    assert "how many" in reply.lower()
    assert plan_confirmation.get_pending(split_user.id) is None


# ── Non-vacuity ─────────────────────────────────────────────────────────────

def test_bypassing_workout_grounding_fails_the_leg_day_test(
        app, split_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import grounding as grounding_mod
    from app.services.coach_plan_tools.workout_targets import WorkoutTarget

    monkeypatch.setattr(
        grounding_mod, "resolve_workout_target",
        lambda user_id, message, model_day=None: WorkoutTarget(
            "resolved", day="Pazartesi", label="chest"))
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges 3x12 to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Pazartesi", "exercise": "Walking Lunges",
            "sets": 3, "reps": "12",
        })
    assert result["status"] == results.STATUS_APPLIED
    assert "Walking Lunge" in names(split_user.id, "Pazartesi")
    assert "Walking Lunge" not in names(split_user.id, "Cuma")


def test_bypassing_exercise_gate_would_write_blabla(
        app, split_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import grounding as grounding_mod
    from app.services.coach_plan_tools.exercise_grounding import ExerciseTarget

    monkeypatch.setattr(
        grounding_mod, "resolve_destination",
        lambda name: ExerciseTarget("resolved", canonical_name=name))
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add blabla 3x10 to Friday.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "blabla",
            "sets": 3, "reps": "10",
        })
    # Canonical plans still refuse unknown names in the domain. The Coach
    # gate is what produces the user-facing unknown copy; bypassing it
    # must not silently persist the string either, but the regression the
    # matrix cares about is "the Coach gate is what stopped the write
    # from being presented as success". On a canonical plan the domain
    # is a second door — prove the Coach test fails open by using a
    # legacy plan below if this still refuses.
    if result.get("status") == results.STATUS_APPLIED:
        assert "blabla" in names(split_user.id, "Cuma")
        return
    # Domain refused: use a legacy plan to show the Coach gate is load-bearing.
    user_id = split_user.id
    db.session.query(TrainingPlan).filter_by(user_id=user_id).delete()
    db.session.commit()
    seed_plan(user_id, _program())
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add blabla 3x10 to Friday.")
        result = call(user_id, ADD, {
            "day": "Cuma", "exercise": "blabla",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_APPLIED
    assert "blabla" in names(user_id, "Cuma")


def test_bypassing_prescription_grounding_writes_fabricated_sets(
        app, split_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import grounding as grounding_mod
    from app.services.coach_plan_tools.prescriptions import Prescription

    monkeypatch.setattr(
        grounding_mod, "merge_prescription",
        lambda user_owned, tool_sets=None, tool_reps=None,
        user_message_present=False: Prescription(
            sets=tool_sets, reps=tool_reps))
    monkeypatch.setattr(
        grounding_mod, "user_owned_intent",
        lambda message=None, history=None: {
            "message": "Add Walking Lunges to my leg workout.",
            "source": "Add Walking Lunges to my leg workout.",
            "exercise": "Walking Lunges",
            "prescription": Prescription(),
            "accepted_proposal": False,
            "has_user_text": False,
        })
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Add Walking Lunges to my leg workout.")
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
    assert result["status"] == results.STATUS_APPLIED
    from app.services.today_facts import get_active_plan
    document = json.loads(get_active_plan(split_user.id).plan_data)
    added = next(d for d in document["program"]
                 if d["gun"] == "Cuma")["egzersizler"][-1]
    assert added["set"] == 3
    assert added["tekrar"] == "10"
