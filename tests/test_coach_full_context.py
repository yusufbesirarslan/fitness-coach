"""P0 context and R1 containment through the real SQLite-backed projections."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import (
    MealLog, MealLogRetainedTotal, WeeklyCheckIn, WorkoutLog, WorkoutSession,
    WORKOUT_COMPLETION_MARKER,
)
from app.services import ai_coach, ai_consent, coach_plan_tools, context_builder
from app.services.coach_nutrition_diary import nutrition_diary
from app.services.mobile_log_food.commands import ManualLogFoodCommand, ManualNutritionSnapshot
from app.services.mobile_log_food.service import log_food
from app.timeutil import app_today, utc_day_bounds
from tests.test_adaptive_coach_foundation_b1 import coach_read_scope, planned_user, turn  # noqa: F401


def _log_manual(owner, key="qualified-entry-001", value="410", *, entered=True):
    return log_food(owner, key, ManualLogFoodCommand(
        "UNSHARED_DIARY_TEXT", "ogle",
        ManualNutritionSnapshot(*(Decimal(amount) for amount in (value, "21", "30", "14"))),
        amounts_entered=entered))[0]


def test_mixed_context_uses_canonical_facts_and_keeps_unrelated_data_out(
        make_user, coach_read_scope):
    owner = make_user("coach-context-owner")
    other = make_user("coach-context-foreign")
    user_id, other_id = owner.id, other.id
    owner.goal = "build_muscle"
    owner.weight = 111.23
    owner.user_metadata = {"equipment_available": ["dumbbell"], "dietary_restrictions": ["vegan"],
                           "other": "UNRELATED_FREE_TEXT"}
    from app.services.exercise_catalog import resolve_exercise

    bench = resolve_exercise(name="Bench Press").exercise_id
    db.session.add(WeeklyCheckIn(user_id=user_id, weight=91.24, fatigue=4))
    today = app_today()
    start, _ = utc_day_bounds(today)
    completed = start + timedelta(hours=3)

    def workout(user_id, status, load, reps):
        sets = [{"index": 0, "completed": True, "weight_kg": load, "reps": reps},
                {"index": 1, "completed": True, "weight_kg": None, "reps": 5},
                {"index": 2, "completed": False, "weight_kg": 92.345, "reps": 722}]
        snapshot = {"exercises": [{"exercise_id": bench, "sets": sets}],
                    "elapsed_seconds": 10, "current_exercise_index": 0}
        return WorkoutSession(user_id=user_id, workout_date=today.isoformat(), status=status,
                              completed_at=completed if status == "completed" else None,
                              checkpoint_revision=1, checkpoint_data=json.dumps(snapshot))
    db.session.add_all([
        workout(user_id, "completed", 64.5, 8),
        workout(user_id, "active", 828, 31),
        workout(other_id, "completed", 932, 33),
    ])
    db.session.commit()
    _log_manual(user_id)
    _log_manual(other_id, value="980")

    with coach_read_scope(user_id):
        context = context_builder.fetch_coach_context(
            user_id, "Compare today's food diary, recent workout progress and sleep", "en")
        single = context_builder.fetch_coach_context(
            user_id, "What is protein and what should I train today?", "en")
        macros = context_builder.fetch_coach_context(user_id, "What are my macros today?", "en")
        body = context_builder.fetch_coach_context(user_id, "How's my weight progress?", "en")
        goals = context_builder.fetch_coach_context(user_id, "My goal, nutrition and sleep", "en")

    for fact in ("build_muscle", "dumbbell", "vegan", '"fatigue": 4', "[PROGRESS CONTEXT]",
                 '"weight_kg": 64.5', '"reps": 8', '"weight_kg": null',
                 '"sets_completed": 2', '"calories": 410.0'):
        assert fact in context
    for absent in ("111.23", "91.24", "UNRELATED_FREE_TEXT", "UNSHARED_DIARY_TEXT", "980.0",
                   "932", "828", "722", '"volume_trend"'):
        assert absent not in context
    assert "[NUTRITION DIARY]" not in single and "[COMPLETED WORKOUTS" not in single
    assert '"logged_totals"' in macros
    assert "[BODY PROGRESS]" in body and "[PROGRESS CONTEXT]" not in body
    assert '"recorded_weight_change_kg": null' in body
    assert "build_muscle" in goals and "dumbbell" not in goals


def test_diary_qualified_amounts_do_not_turn_partial_data_into_totals(make_user):
    owner = make_user("coach-diary-owner")
    user_id = owner.id
    today = app_today()
    assert nutrition_diary(user_id, today) == {
        "day": today.isoformat(), "status": "empty", "entries": []}
    manual = _log_manual(user_id)
    result = nutrition_diary(user_id, today)
    assert result["status"] == "available"
    assert result["logged_totals"] == {"calories": 410.0, "protein_g": 21.0,
                                       "carbs_g": 30.0, "fat_g": 14.0}
    for source in ("manual", "menu_estimated", "search"):
        db.session.add(MealLog(
            user_id=user_id, source=source, tarih=today.isoformat(), ogun="PRIVATE_SLOT",
            yemekler="PRIVATE_FOOD", kalori=893.55, protein=97, karb=58, yag=38))
    db.session.commit()
    result = nutrition_diary(user_id, today)
    assert result["status"] == "unavailable" and "logged_totals" not in result
    assert result["entries"] == [{"slot": "ogle", "logged_nutrition": {
        "calories": 410.0, "protein_g": 21.0, "carbs_g": 30.0, "fat_g": 14.0}}]
    manual.protein = None
    db.session.commit()
    assert "protein_g" not in nutrition_diary(user_id, today)["entries"][0]["logged_nutrition"]
    previous = today - timedelta(days=1)
    db.session.add(MealLogRetainedTotal(user_id=user_id, tarih=previous.isoformat(),
                                       energy_kcal=891, item_count=1))
    db.session.commit()
    assert nutrition_diary(user_id, previous) == {
        "day": previous.isoformat(), "status": "unavailable", "entries": []}


def test_loaded_r1_guard_keeps_plan_writes_closed_even_when_read_authorized(
        app, planned_user, turn, coach_read_scope, monkeypatch):
    monkeypatch.setitem(app.extensions, "r6_coach_containment", True)
    monkeypatch.setitem(app.config, coach_plan_tools.FLAG_KEY, True)
    user_id = planned_user.id
    with coach_read_scope(user_id):
        defs = ai_coach._coach_tool_defs_for_call(user_id)
        names = {tool["name"] for tool in defs}
        assert coach_plan_tools.READ_PLAN_TOOL in names
        assert not names.intersection(coach_plan_tools.PLAN_WRITE_TOOL_NAMES)
        for name in coach_plan_tools.PLAN_WRITE_TOOL_NAMES:
            result = json.loads(ai_coach._dispatch_coach_tool(user_id, name, '{}'))
            assert result["error"] == "CAPABILITY_DISABLED"
        assert coach_plan_tools.execute_direct_confirmation(
            user_id, "opaque-proposal", "confirm")["error"] == "CAPABILITY_DISABLED"
        assert coach_plan_tools.execute_plan_read(user_id)["status"] == "ok"


def test_expanded_context_checks_authority_before_read(make_user, monkeypatch):
    owner, other = make_user("scope-owner"), make_user("scope-other")
    foreign = ai_consent.OperationAuthority(other.id, "coach", 1, "d1-closure02-coach-candidate-r2")
    with ai_consent.operation_scope(foreign), pytest.raises(ai_consent.ConsentDenied):
        context_builder.fetch_coach_context(owner.id, "nutrition diary", "en")
    with ai_consent.operation_scope(foreign), pytest.raises(ai_consent.ConsentDenied):
        coach_plan_tools.execute_plan_read(owner.id)


def test_legacy_repeat_and_explicit_copies_cannot_create_qualified_entries(make_user):
    from app.services.mobile_log_food import parse_command

    owner = make_user("coach-repeat-owner")
    user_id = owner.id
    today = app_today()
    earlier = (today - timedelta(days=1)).isoformat()
    for source, label in (("menu_estimated", "ESTIMATE"), ("diary", "EXPIRED PROVIDER SUMMARY")):
        db.session.add(MealLog(
            user_id=user_id, tarih=earlier, source=source, yemekler=label, ogun="Öğle",
            kalori=410, protein=21, karb=30, yag=14,
            provider_expired_at=utc_day_bounds(today)[0] if source == "diary" else None))
    db.session.commit()
    for count, (kind, name) in enumerate((
            ("manual", "ESTIMATE"), ("manual_entered", "ESTIMATE"),
            ("manual_entered", "EXPIRED PROVIDER SUMMARY"))):
        command = parse_command({
            "kind": kind, "description": name, "slot": "ogle",
            "nutrition": {"energy_kcal": 410, "protein_g": 21, "carbohydrate_g": 30, "fat_g": 14},
        })
        row, _ = log_food(user_id, f"diary-copy-{count}", command)
        assert row.provider_items is None
    assert nutrition_diary(user_id, today) == {
        "day": today.isoformat(), "status": "unavailable", "entries": []}


@pytest.mark.parametrize("question", ["Show my workout progress", "Review my workout"])
def test_completion_markers_never_supply_a_measured_flat_progress_trend(
        app, make_user, coach_read_scope, monkeypatch, question):
    monkeypatch.setitem(app.config, "AI_ADAPTIVE_PLAN_CONTEXT", True)
    user_id = make_user("coach-marker-history").id
    today = app_today()
    for offset in (0, 7, 14):
        start, _ = utc_day_bounds(today - timedelta(days=offset))
        db.session.add(WorkoutLog(
            user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER,
            sets=1, reps=1, weight_kg=0, volume=0, created_at=start + timedelta(hours=1)))
    db.session.commit()
    with coach_read_scope(user_id):
        context = context_builder.fetch_coach_context(user_id, question, "en")
    assert "Insufficient measured workout history to establish a trend." in context
    assert "holding steady" not in context.lower()
    assert "volume_trend" not in context
