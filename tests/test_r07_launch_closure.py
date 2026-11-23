import json

import pytest

from app.extensions import db
from app.services import coach_confirmation, coach_plan_tools, plan_confirmation
from app.services.mobile_training import _rest
from app.services.today_facts import get_active_plan
from tests.test_coach_plan_confirmation import _new_turn
from tests.test_coach_plan_tool_characterization import planned_user  # noqa: F401
from tests.test_coach_plan_tools import (
    ADD, MOVE, PRESCRIBE, REPLACE, call, canonical_user, journal, tools_on, turn,  # noqa: F401
)


@pytest.mark.parametrize(("tool", "changes"), [
    (PRESCRIBE, {"sets": 100}),
    (REPLACE, {"replacement": "Push-Up", "sets": 100}),
    (ADD, {"exercise": "Push-Up", "sets": 100, "reps": "8"}),
    (PRESCRIBE, {"reps": "note: tender knee"}),
    (PRESCRIBE, {"reps": "15-8"}),
    (PRESCRIBE, {"reps": "1000"}),
    (PRESCRIBE, {"reps": "61 min"}),
])
def test_invalid_prescriptions_do_not_propose_or_persist(planned_user, tools_on, turn, tool, changes):
    owner = planned_user.id
    baseline = get_active_plan(owner).plan_data
    result = call(owner, tool, dict({"day": "Pazartesi", "exercise": "Bench Press"}, **changes))
    assert result["error"] == "INVALID_PRESCRIPTION"
    assert get_active_plan(owner).plan_data == baseline
    assert not journal(owner) and plan_confirmation.get_pending(owner) is None


@pytest.mark.parametrize("reps", ["8-12", "45 sn", "30 dk"])
def test_canonical_add_requires_preview_and_keeps_rest_unspecified(
        app, tools_on, turn, canonical_user, reps):
    owner = canonical_user.id
    baseline = get_active_plan(owner).plan_data
    result = call(owner, ADD, {"day": "Çarşamba", "exercise": "Band Row", "sets": 3, "reps": reps})
    assert result["status"] == "confirmation_required", result
    preview = coach_confirmation.reply_after_tools(owner, "en", [result])
    assert "Resistance Band Row" in preview and f"target {reps}" in preview
    assert "Rest between sets will be left unspecified" in preview
    assert get_active_plan(owner).plan_data == baseline and not journal(owner)
    assert call(owner, coach_plan_tools.CONFIRM_TOOL)["error"] == "CONFIRMATION_NOT_AUTHORIZED"
    _new_turn(app, "yes")
    assert call(owner, coach_plan_tools.CONFIRM_TOOL)["status"] == "applied"
    exercises = json.loads(get_active_plan(owner).plan_data)["program"][2]["egzersizler"]
    added = exercises[-1]
    assert added["exercise_id"] == "ex_band_row" and added["not"] == ""
    assert _rest(added["dinlenme"])["seconds"] is None


def test_rest_swap_preview_and_roundtrip_preserve_both_slots(app, tools_on, turn, canonical_user):
    owner = canonical_user.id
    plan = get_active_plan(owner)
    document = json.loads(plan.plan_data)
    workout, rest = document["program"][:2]
    workout["egzersizler"][0]["not"] = "Keep tempo"
    rest["odak"] = "Recovery"
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()

    result = call(owner, MOVE, {"day": "Salı", "target_day": "Pazartesi"})
    preview = coach_confirmation.reply_after_tools(owner, "en", [result])
    assert "rest day from Tuesday to Monday" in preview
    assert "Monday session to Tuesday" in preview
    assert json.loads(get_active_plan(owner).plan_data) == document
    _new_turn(app, "yes")
    assert call(owner, coach_plan_tools.CONFIRM_TOOL)["status"] == "applied"
    saved = json.loads(get_active_plan(owner).plan_data)
    assert saved["program"][:2] == [dict(rest, gun="Pazartesi"), dict(workout, gun="Salı")]
    assert saved["program"][2:] == document["program"][2:]
    _new_turn(app)
    undo = call(owner, "undo_last_training_plan_change")
    assert undo["status"] == "confirmation_required"
    assert "restore the previous" in coach_confirmation.reply_after_tools(owner, "en", [undo])
    assert json.loads(get_active_plan(owner).plan_data) == saved
    _new_turn(app, "yes")
    assert call(owner, coach_plan_tools.CONFIRM_TOOL)["status"] == "applied"
    assert json.loads(get_active_plan(owner).plan_data) == document


def test_malformed_rest_day_is_neither_an_exercise_target_nor_swappable(
        planned_user, tools_on, turn):
    owner = planned_user.id
    plan = get_active_plan(owner)
    document = json.loads(plan.plan_data)
    document["program"][1]["egzersizler"] = document["program"][0]["egzersizler"][:1]
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    moved = call(owner, MOVE, {"day": "Salı", "target_day": "Pazartesi"})
    edited = call(owner, PRESCRIBE, {"day": "Salı", "exercise": "Bench Press", "sets": 4})
    assert moved["error"] == edited["error"] == "INVALID_MUTATION"
    assert json.loads(get_active_plan(owner).plan_data) == document


@pytest.mark.parametrize("utterance", ["1.5 sets", "-5 reps", "3x8.5", "3,5 sets", "3x8–-12"])
def test_malformed_user_prescriptions_do_not_donate_suffix_digits(utterance):
    from app.services.coach_plan_tools.prescriptions import Prescription, parse_prescription
    assert parse_prescription(utterance) == Prescription()


@pytest.mark.parametrize(("utterance", "target"), [
    ("Add plank 3 sets of 30 seconds to Monday", "30 sn"),
    ("Pazartesiye 3x45 sn plank ekle", "45 sn"),
])
def test_timed_user_prescriptions_reach_the_material_preview(app, planned_user, tools_on, utterance, target):
    with app.test_request_context("/ask"):
        _new_turn(app, utterance)
        result = call(planned_user.id, ADD, {"day": "Pazartesi", "exercise": "Plank"})
        preview = coach_confirmation.reply_after_tools(planned_user.id, "en", [result])
    assert result["status"] == "confirmation_required", result
    assert f"3 sets, target {target}" in preview
    assert not journal(planned_user.id)


def test_canonical_replace_cannot_duplicate_an_exercise(canonical_user, tools_on, turn):
    owner = canonical_user.id
    result = call(owner, REPLACE, {
        "day": "Pazartesi", "exercise": "Dumbbell Row", "replacement": "Push-Up",
    })
    assert result["error"] == "EXERCISE_ALREADY_PRESENT"
    assert not journal(owner) and plan_confirmation.get_pending(owner) is None


def test_coach_add_respects_the_editor_slot_limit(canonical_user, tools_on, turn):
    owner = canonical_user.id
    plan = get_active_plan(owner)
    document = json.loads(plan.plan_data)
    exercises = document["program"][0]["egzersizler"]
    # Use legacy repeated slots to exercise the cap without editing any of them.
    exercises.extend([dict(exercises[0]) for _ in range(30)])
    plan.plan_data = json.dumps(document, ensure_ascii=False)
    db.session.commit()
    result = call(owner, ADD, {"day": "Pazartesi", "exercise": "Band Row", "sets": 3, "reps": "8"})
    assert result["error"] == "INVALID_MUTATION"
    assert not journal(owner) and plan_confirmation.get_pending(owner) is None


def test_typed_yes_cannot_target_replacement_training_proposal(
        app, planned_user, tools_on, turn, monkeypatch):
    owner = planned_user.id
    baseline = get_active_plan(owner).plan_data
    first = call(owner, PRESCRIBE, {"day": "Pazartesi", "exercise": "Bench Press", "sets": 4})
    assert first["status"] == "confirmation_required"
    captured_id = plan_confirmation.get_pending(owner).public_id
    confirm_observed = coach_confirmation._confirm_plan

    def cancel_and_replace_before_confirmation(user_id, language):
        plan_confirmation.cancel_pending(owner)
        with app.test_request_context("/ask"):
            _new_turn(app)
            replacement = call(
                owner, PRESCRIBE, {"day": "Pazartesi", "exercise": "Bench Press", "sets": 5})
            assert replacement["status"] == "confirmation_required"
        return confirm_observed(user_id, language)

    monkeypatch.setattr(coach_confirmation, "_confirm_plan", cancel_and_replace_before_confirmation)
    _new_turn(app, "yes")
    reply = coach_confirmation.resolve_pending_turn(owner, "en")

    assert "no longer valid" in reply
    remaining = plan_confirmation.get_pending(owner)
    assert remaining.public_id != captured_id and remaining.command_payload["sets"] == 5
    assert get_active_plan(owner).plan_data == baseline and not journal(owner)
