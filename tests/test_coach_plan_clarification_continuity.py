"""Multi-turn Coach plan-mutation clarification continuity.

Turn 2 is a NEW HTTP request. Flask request-local session is not enough:
grounded fields must survive on a user-scoped server store. Persistence
assertions are on the plan row, journal, proposals and workout logs.
"""
import json
import time

import pytest
from flask import session as flask_session

from app.extensions import db
from app.models import (
    TrainingPlanConfirmationProposal,
    WorkoutLog,
)
from app.observability import assign_request_id
from app.services import ai_coach, coach_confirmation, plan_confirmation
from app.services.coach_plan_tools import clarifications as clar_mod
from app.services.coach_plan_tools import results
from app.services.plan_mutation.validation import WEEKDAYS
from app.services.today_facts import get_active_plan
from tests.test_coach_plan_mutation_grounding import (
    _ambiguous_legs_program,
    _assert_unchanged,
    _snapshot,
    _split_program,
)
from tests.test_coach_plan_tools import (  # noqa: F401
    ADD, PRESCRIBE, REPLACE, call, journal, names, plan_version, seed_plan,
    tools_on,
)


TURKISH_WEEKDAYS = WEEKDAYS


@pytest.fixture
def split_user(app, make_user):
    user = make_user("continuitycoach")
    seed_plan(user.id, _split_program())
    return user


def _fresh_turn(app, message, history=None, user_id=None):
    assign_request_id()
    ai_coach._begin_coach_turn(
        message, history=history or [], user_id=user_id)


def _turn1_add(app, user_id, message, arguments=None):
    arguments = arguments or {
        "day": "Pazartesi",
        "exercise": "Walking Lunges",
        "sets": 3,
        "reps": "10",
    }
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, message, user_id=user_id)
        result = call(user_id, ADD, arguments)
        reply = coach_confirmation.reply_after_tools(user_id, "en", [result])
    return result, reply


def _turn2(app, user_id, message, language="en"):
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, message, user_id=user_id)
        return coach_confirmation.resolve_pending_turn(user_id, language)


def _friday_slot(user_id):
    db.session.expire_all()
    document = json.loads(get_active_plan(user_id).plan_data)
    return next(d for d in document["program"] if d["gun"] == "Cuma")


def _day_slot(user_id, day):
    db.session.expire_all()
    document = json.loads(get_active_plan(user_id).plan_data)
    return next(d for d in document["program"] if d["gun"] == day)


def _assert_no_english_turkish_weekday(text):
    lowered = text.lower()
    for day in TURKISH_WEEKDAYS:
        assert day.lower() not in lowered, text


# ── 1. Missing both + yes accepts server-owned 3x8-12 ───────────────────────

def test_missing_both_then_yes_it_would_be_good_applies_proposal(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    result, reply = _turn1_add(
        app, split_user.id, "Add Walking Lunges to my leg workout.")
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_MISSING_PRESCRIPTION
    _assert_unchanged(split_user.id, before)
    assert plan_confirmation.get_pending(split_user.id) is None
    _assert_no_english_turkish_weekday(reply)

    applied = _turn2(app, split_user.id, "yes it would be good")
    assert applied is not None
    added = _friday_slot(split_user.id)["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert added["exercise_id"] == "ex_walking_lunge"
    assert added["set"] == 3
    assert added["tekrar"] == "8-12"
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1
    assert WorkoutLog.query.filter_by(user_id=split_user.id).count() == 0
    assert TrainingPlanConfirmationProposal.query.filter_by(
        user_id=split_user.id).count() == 0


def test_yes_without_server_proposal_does_not_invent_prescription(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(
        app, split_user.id,
        "Add Walking Lunges with 4 sets to my leg workout.",
        {"day": "Cuma", "exercise": "Walking Lunges", "sets": 3, "reps": "10"})
    reply = _turn2(app, split_user.id, "yes")
    if reply is not None:
        added_names = names(split_user.id, "Cuma")
        assert "Walking Lunge" not in added_names or _snapshot(
            split_user.id) == before
    _assert_unchanged(split_user.id, before)


# ── 2. Partial prescription: bare "15" completes stored 4 sets ───────────────

def test_partial_sets_then_bare_reps_writes_4x15(app, split_user, tools_on):
    before = _snapshot(split_user.id)
    result, reply = _turn1_add(
        app, split_user.id,
        "Add Walking Lunges with 4 sets to my leg workout.",
        {"day": "Cuma", "exercise": "Walking Lunges", "sets": 3, "reps": "10"})
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_MISSING_REPS
    assert "how many reps" in reply.lower()
    _assert_unchanged(split_user.id, before)

    applied = _turn2(app, split_user.id, "15")
    assert applied is not None
    added = _friday_slot(split_user.id)["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert added["set"] == 4
    assert added["tekrar"] == "15"
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1


# ── 3–4. Ambiguous day preserves 3x12; invalid day refuses ───────────────────

def test_ambiguous_day_then_friday_preserves_prescription(
        app, make_user, tools_on):
    user = make_user("twolegscont")
    seed_plan(user.id, _ambiguous_legs_program())
    before = _snapshot(user.id)
    result, reply = _turn1_add(
        app, user.id,
        "Add Walking Lunge 3x12 to my lower body workout.",
        {"day": "Cuma", "exercise": "Walking Lunge", "sets": 3, "reps": "12"})
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_AMBIGUOUS_WORKOUT
    assert "more than one" in reply.lower()
    _assert_no_english_turkish_weekday(reply)
    _assert_unchanged(user.id, before)

    applied = _turn2(app, user.id, "Friday")
    assert applied is not None
    friday = _friday_slot(user.id)
    added = friday["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert added["set"] == 3
    assert added["tekrar"] == "12"
    wednesday = _day_slot(user.id, "Çarşamba")
    assert all(e["isim"] != "Walking Lunge" for e in wednesday["egzersizler"])
    assert plan_version(user.id) == before[1] + 1
    assert len(journal(user.id)) == 1


def test_ambiguous_day_rejects_non_candidate_sunday(
        app, make_user, tools_on):
    user = make_user("twolegsrefuse")
    seed_plan(user.id, _ambiguous_legs_program())
    before = _snapshot(user.id)
    _turn1_add(
        app, user.id,
        "Add Walking Lunge 3x12 to my lower body workout.",
        {"day": "Cuma", "exercise": "Walking Lunge", "sets": 3, "reps": "12"})
    reply = _turn2(app, user.id, "Sunday")
    assert reply is not None
    _assert_unchanged(user.id, before)
    assert "Walking Lunge" not in names(user.id, "Cuma")
    assert "Walking Lunge" not in names(user.id, "Çarşamba")
    assert "Walking Lunge" not in names(user.id, "Pazar")


# ── 5–7. Exercise suggestion, rejection, unknown ─────────────────────────────

def test_dambil_curl_suggestion_then_yes_writes_canonical_3x10(
        app, make_user, tools_on):
    user = make_user("armcurl")
    program = _split_program()
    program["program"][0]["odak"] = "Chest"
    program["program"][0]["egzersizler"] = [
        {"isim": "Push-Up", "set": 3, "tekrar": "10"}]
    program["program"][2]["odak"] = "Back"
    seed_plan(user.id, program)
    before = _snapshot(user.id)
    result, reply = _turn1_add(
        app, user.id,
        "Add dambıl curl 3x10 to my Monday workout.",
        {"day": "Cuma", "exercise": "dambıl curl", "sets": 3, "reps": "10"})
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_EXERCISE_SUGGEST
    assert "did you mean" in reply.lower()
    assert "dumbbell" in reply.lower()
    _assert_unchanged(user.id, before)
    assert plan_confirmation.get_pending(user.id) is None

    applied = _turn2(app, user.id, "yes")
    assert applied is not None
    added = _day_slot(user.id, "Pazartesi")["egzersizler"][-1]
    assert added["isim"] == "Dumbbell Biceps Curl"
    assert added["set"] == 3
    assert added["tekrar"] == "10"
    assert plan_version(user.id) == before[1] + 1
    assert len(journal(user.id)) == 1
    assert names(user.id, "Cuma") == ["Bodyweight Squat"]


def test_suggestion_rejection_clears_and_does_not_mutate(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(
        app, split_user.id,
        "Add walkinglungez 3x10 to Friday.",
        {"day": "Pazartesi", "exercise": "walkinglungez",
         "sets": 3, "reps": "10"})
    reply = _turn2(app, split_user.id, "no")
    _assert_unchanged(split_user.id, before)
    later = _turn2(app, split_user.id, "yes")
    _assert_unchanged(split_user.id, before)
    assert later is None or "Walking Lunge" not in names(
        split_user.id, "Cuma")


def test_unknown_exercise_stores_no_executable_state(app, split_user, tools_on):
    before = _snapshot(split_user.id)
    result, reply = _turn1_add(
        app, split_user.id,
        "Add blabla 3x10 to my leg workout.",
        {"day": "Cuma", "exercise": "blabla", "sets": 3, "reps": "10"})
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_EXERCISE_UNKNOWN
    _assert_unchanged(split_user.id, before)
    assert "couldn't find" in reply.lower()
    assert _turn2(app, split_user.id, "yes") is None
    _assert_unchanged(split_user.id, before)


# ── 8. Replace without sets/reps inherits existing prescription ──────────────

def _curl_friday_program():
    program = _split_program()
    program["program"][4]["odak"] = "Arms"
    program["program"][4]["egzersizler"] = [
        {"isim": "Hammer Curl", "set": 3, "tekrar": "8",
         "exercise_id": "ex_hammer_curl"}]
    return program


def test_replace_without_sets_reps_inherits_prescription(
        app, make_user, tools_on):
    user = make_user("replacecurl")
    seed_plan(user.id, _curl_friday_program())
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(
            app,
            "Replace Hammer Curl with Dumbbell Curl in my Friday workout.",
            user_id=user.id)
        result = call(user.id, REPLACE, {
            "day": "Cuma",
            "exercise": "Hammer Curl",
            "replacement": "Dumbbell Curl",
        })
        reply = coach_confirmation.reply_after_tools(user.id, "en", [result])
    assert result["status"] == results.STATUS_APPLIED, result
    added = _friday_slot(user.id)["egzersizler"]
    assert [e["isim"] for e in added] == ["Dumbbell Biceps Curl"]
    assert added[0]["set"] == 3
    assert added[0]["tekrar"] == "8"
    assert plan_version(user.id) == before[1] + 1
    assert len(journal(user.id)) == 1
    assert "dumbbell" in reply.lower()
    _assert_no_english_turkish_weekday(reply)


def test_route_replace_without_sets_reps_does_not_ask_for_them(
        app, make_user, tools_on, monkeypatch):
    from tests.test_ai_coach import _ScriptedLLM, _llm_msg, _tool_call

    user = make_user("replacecoach")
    seed_plan(user.id, _curl_friday_program())
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", False)
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        monkeypatch.setattr(ai_coach, "openai_client", _ScriptedLLM([
            _llm_msg(tool_calls=[_tool_call(
                "replace_training_plan_exercise",
                json.dumps({
                    "day": "Pazartesi",
                    "exercise": "Hammer Curl",
                    "replacement": "Dumbbell Curl",
                }))]),
            _llm_msg("Would you like me to replace Hammer Curl?"),
        ]))
        reply = ai_coach._run_coach_conversation(
            user.id,
            "Replace Hammer Curl with Dumbbell Curl in my Friday workout.",
            "", client_history=[], language="en")
    added = _friday_slot(user.id)["egzersizler"]
    assert [e["isim"] for e in added] == ["Dumbbell Biceps Curl"]
    assert added[0]["set"] == 3
    assert added[0]["tekrar"] == "8"
    assert plan_version(user.id) == before[1] + 1
    assert "repeat the exercise, day, sets, and reps" not in reply.lower()
    _assert_no_english_turkish_weekday(reply)


# ── 9. Update prescription uniquely resolves the slot ────────────────────────

def test_update_prescription_resolves_unique_slot(app, make_user, tools_on):
    user = make_user("updatecurl")
    seed_plan(user.id, _curl_friday_program())
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Change Hammer Curl to 4x10", user_id=user.id)
        result = call(user.id, PRESCRIBE, {
            "day": "Pazartesi",
            "exercise": "Hammer Curl",
            "sets": 4,
            "reps": "10",
        })
    assert result["status"] == results.STATUS_APPLIED, result
    added = _friday_slot(user.id)["egzersizler"][0]
    assert added["isim"] == "Hammer Curl"
    assert added["set"] == 4
    assert added["tekrar"] == "10"
    assert plan_version(user.id) == before[1] + 1
    assert len(journal(user.id)) == 1
    assert _day_slot(user.id, "Pazartesi")["egzersizler"][0]["isim"] == "Push-Up"


def test_update_prescription_asks_when_exercise_is_on_two_days(
        app, make_user, tools_on):
    user = make_user("updateambig")
    program = _curl_friday_program()
    program["program"][0]["egzersizler"] = [
        {"isim": "Hammer Curl", "set": 3, "tekrar": "8",
         "exercise_id": "ex_hammer_curl"}]
    seed_plan(user.id, program)
    before = _snapshot(user.id)
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Change Hammer Curl to 4x10", user_id=user.id)
        result = call(user.id, PRESCRIBE, {
            "day": "Pazartesi",
            "exercise": "Hammer Curl",
            "sets": 4,
            "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(user.id, "en", [result])
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert result["reason"] == results.REASON_AMBIGUOUS_WORKOUT
    _assert_unchanged(user.id, before)
    _assert_no_english_turkish_weekday(reply)


# ── 10–11. i18n ──────────────────────────────────────────────────────────────

def test_english_missing_rx_copy_does_not_leak_turkish_weekday(
        app, split_user, tools_on):
    result, reply = _turn1_add(
        app, split_user.id, "Add Walking Lunges to my Monday workout.")
    assert result["status"] == results.STATUS_NEEDS_INPUT
    assert "monday" in reply.lower()
    _assert_no_english_turkish_weekday(reply)


def test_turkish_copy_keeps_canonical_weekdays(app, split_user, tools_on):
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(
            app, "Pazartesi antrenmanıma Walking Lunges ekle.",
            user_id=split_user.id)
        result = call(split_user.id, ADD, {
            "day": "Cuma", "exercise": "Walking Lunges",
            "sets": 3, "reps": "10",
        })
        reply = coach_confirmation.reply_after_tools(
            split_user.id, "tr", [result])
    assert "Pazartesi" in reply
    assert "Monday" not in reply


# ── 12. Stale clarification does not fire on unrelated chat ──────────────────

def test_unrelated_turn_does_not_apply_stale_clarification(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    reply = _turn2(app, split_user.id, "What is a good protein source?")
    _assert_unchanged(split_user.id, before)
    assert reply is None
    later = _turn2(app, split_user.id, "yes")
    _assert_unchanged(split_user.id, before)
    assert later is None


# ── Persistence on clarification turns ───────────────────────────────────────

def test_clarification_turn_does_not_create_a_proposal(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    _assert_unchanged(split_user.id, before)
    assert plan_confirmation.get_pending(split_user.id) is None
    assert WorkoutLog.query.filter_by(user_id=split_user.id).count() == 0


# ── Non-vacuity ──────────────────────────────────────────────────────────────

def test_bypassing_continuation_state_loses_bare_reps(
        app, split_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import clarifications as clar_mod

    _turn1_add(
        app, split_user.id,
        "Add Walking Lunges with 4 sets to my leg workout.",
        {"day": "Cuma", "exercise": "Walking Lunges", "sets": 3, "reps": "10"})
    monkeypatch.setattr(clar_mod, "load", lambda user_id=None: None)
    monkeypatch.setattr(clar_mod, "load_current", lambda: None)
    reply = _turn2(app, split_user.id, "15")
    assert reply is None
    assert "Walking Lunge" not in names(split_user.id, "Cuma")


def test_bypassing_unique_slot_resolution_breaks_update(
        app, make_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import grounding as grounding_mod
    from app.services.coach_plan_tools.workout_targets import WorkoutTarget

    user = make_user("updatevacuity")
    seed_plan(user.id, _curl_friday_program())
    monkeypatch.setattr(
        grounding_mod, "resolve_workout_target",
        lambda user_id, message, model_day=None: WorkoutTarget("not_found"))
    monkeypatch.setattr(
        grounding_mod, "find_exercise_slots",
        lambda user_id, name: ())
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(app, "Change Hammer Curl to 4x10", user_id=user.id)
        result = call(user.id, PRESCRIBE, {
            "day": "Pazartesi",
            "exercise": "Hammer Curl",
            "sets": 4,
            "reps": "10",
        })
    assert result["status"] != results.STATUS_APPLIED


def test_bypassing_replace_user_day_grounding_misses_friday(
        app, make_user, tools_on, monkeypatch):
    from app.services.coach_plan_tools import grounding as grounding_mod
    from app.services.coach_plan_tools.workout_targets import WorkoutTarget

    user = make_user("replacevacuity")
    seed_plan(user.id, _curl_friday_program())
    monkeypatch.setattr(
        grounding_mod, "resolve_workout_target",
        lambda user_id, message, model_day=None: WorkoutTarget(
            "resolved", day="Pazartesi", label="Pazartesi"))
    with app.test_request_context("/ask", method="POST"):
        _fresh_turn(
            app,
            "Replace Hammer Curl with Dumbbell Curl in my Friday workout.",
            user_id=user.id)
        result = call(user.id, REPLACE, {
            "day": "Pazartesi",
            "exercise": "Hammer Curl",
            "replacement": "Dumbbell Curl",
        })
    assert result["status"] != results.STATUS_APPLIED or (
        "Dumbbell Biceps Curl" not in names(user.id, "Cuma"))


# ── Production continuation authority ─────────────────────────────────────────

class _ClarificationRedis:
    """Shared-store stand-in. ``fail=True`` is a production Redis outage."""

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    def get(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        if self.fail:
            raise RuntimeError("redis down")
        self.store[key] = value
        return True

    def delete(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        self.store.pop(key, None)
        return 1

    def getdel(self, key):
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.pop(key, None)


def _executable_add_record(user_id, **overrides):
    record = {
        "user_id": int(user_id),
        "operation": "add_exercise",
        "day": "Cuma",
        "exercise": "Walking Lunge",
        "replacement": "",
        "suggestion": "",
        "sets": 3,
        "reps": "8-12",
        "proposed_sets": 3,
        "proposed_reps": "8-12",
        "candidate_days": [],
        "reason": results.REASON_MISSING_PRESCRIPTION,
        "created_at": time.time(),
    }
    record.update(overrides)
    return record


def _assert_no_mutation(user_id, before):
    _assert_unchanged(user_id, before)
    assert "Walking Lunge" not in names(user_id, "Cuma")
    assert len(journal(user_id)) == 0
    assert plan_confirmation.get_pending(user_id) is None
    assert TrainingPlanConfirmationProposal.query.filter_by(
        user_id=user_id).count() == 0
    assert WorkoutLog.query.filter_by(user_id=user_id).count() == 0


def test_cross_worker_shared_authority_applies_once(
        app, split_user, tools_on, monkeypatch):
    """Create on worker A, resume on worker B via the shared store only."""
    shared = _ClarificationRedis()
    worker_a = {}
    worker_b = {}
    monkeypatch.setattr(clar_mod, "_redis", lambda: shared)
    monkeypatch.setattr(clar_mod, "_MEMORY", worker_a)

    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    assert any(shared.store.values())

    monkeypatch.setattr(clar_mod, "_MEMORY", worker_b)
    applied = _turn2(app, split_user.id, "yes it would be good")
    assert applied is not None
    added = _friday_slot(split_user.id)["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert added["set"] == 3
    assert added["tekrar"] == "8-12"
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1

    after = _snapshot(split_user.id)
    again = _turn2(app, split_user.id, "yes")
    _assert_unchanged(split_user.id, after)
    assert len(journal(split_user.id)) == 1
    assert again is None or "added" not in (again or "").lower() or (
        "nothing was changed" in (again or "").lower())


def test_shared_store_outage_fail_closed_does_not_mutate(
        app, split_user, tools_on, monkeypatch):
    """Redis is configured but down: local copies must not execute."""
    down = _ClarificationRedis(fail=True)
    local = {split_user.id: _executable_add_record(split_user.id)}
    monkeypatch.setattr(clar_mod, "_redis", lambda: down)
    monkeypatch.setattr(clar_mod, "_MEMORY", local)
    before = _snapshot(split_user.id)

    reply = _turn2(app, split_user.id, "yes it would be good")
    _assert_no_mutation(split_user.id, before)
    assert reply is not None
    lowered = reply.lower()
    assert "nothing was changed" in lowered
    assert "try again" in lowered
    assert "has been added" not in lowered


def test_stale_local_copy_cannot_execute_when_shared_record_is_gone(
        app, split_user, tools_on, monkeypatch):
    """Redis miss (consumed/expired/cleared) must not revive process-local state."""
    shared = _ClarificationRedis()
    monkeypatch.setattr(clar_mod, "_redis", lambda: shared)
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    record = json.loads(next(iter(shared.store.values())))
    shared.store.clear()
    monkeypatch.setattr(clar_mod, "_MEMORY", {split_user.id: record})

    reply = _turn2(app, split_user.id, "yes it would be good")
    _assert_no_mutation(split_user.id, before)
    assert reply is None or "has been added" not in (reply or "").lower()


def test_session_copy_is_not_executable_when_shared_record_is_gone(
        app, split_user, tools_on, monkeypatch):
    """A leftover signed session blob is transport, not mutation authority."""
    shared = _ClarificationRedis()
    monkeypatch.setattr(clar_mod, "_redis", lambda: shared)
    before = _snapshot(split_user.id)
    record = _executable_add_record(split_user.id)
    monkeypatch.setattr(clar_mod, "_MEMORY", {})

    with app.test_request_context("/ask", method="POST"):
        flask_session[clar_mod._KEY] = record
        flask_session.modified = True
        _fresh_turn(app, "yes it would be good", user_id=split_user.id)
        reply = coach_confirmation.resolve_pending_turn(split_user.id, "en")

    _assert_no_mutation(split_user.id, before)
    assert reply is None or "has been added" not in (reply or "").lower()


def test_successful_yes_consumes_state_so_a_second_yes_cannot_replay(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    first = _turn2(app, split_user.id, "yes it would be good")
    assert first is not None
    added = _friday_slot(split_user.id)["egzersizler"][-1]
    assert added["isim"] == "Walking Lunge"
    assert plan_version(split_user.id) == before[1] + 1
    assert len(journal(split_user.id)) == 1

    after = _snapshot(split_user.id)
    second = _turn2(app, split_user.id, "yes")
    _assert_unchanged(split_user.id, after)
    assert len(journal(split_user.id)) == 1
    assert names(split_user.id, "Cuma").count("Walking Lunge") == 1
    assert second is None or "has been added" not in (second or "").lower() or (
        "nothing was changed" in (second or "").lower())


def test_cancelled_clarification_is_not_executable(
        app, split_user, tools_on):
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    cancelled = _turn2(app, split_user.id, "no")
    _assert_no_mutation(split_user.id, before)
    later = _turn2(app, split_user.id, "yes it would be good")
    _assert_no_mutation(split_user.id, before)
    assert later is None or "has been added" not in (later or "").lower()
    assert cancelled is None or "has been added" not in (cancelled or "").lower()


def test_expired_clarification_is_not_executable(
        app, split_user, tools_on, monkeypatch):
    before = _snapshot(split_user.id)
    _turn1_add(app, split_user.id, "Add Walking Lunges to my leg workout.")
    now = time.time()
    monkeypatch.setattr(
        clar_mod.time, "time", lambda: now + clar_mod._TTL_SECONDS + 5)
    reply = _turn2(app, split_user.id, "yes it would be good")
    _assert_no_mutation(split_user.id, before)
    assert reply is None or "has been added" not in (reply or "").lower()
