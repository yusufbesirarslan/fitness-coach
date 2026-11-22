"""Operation-specific, owner-scoped Coach context (PA06).

Social queries and arbitrary stored prose are intentionally absent. Domain/UI
serializers remain available to non-AI callers.
"""
import json
import re
from datetime import timedelta
from flask import current_app
from app.extensions import db
from app.models import User, WeeklyCheckIn
from app.services import ai_consent, coach_privacy as privacy
from app.services.coach_privacy import assert_principal
from app.services.workout_state import resolve_workout_state
from app.timeutil import app_today


def fetch_profile_and_trends(user_id, selected_operation="training"):
    assert_principal(user_id)
    user = db.session.get(User, user_id)
    if user is None:
        return []
    meta = user.user_metadata if isinstance(user.user_metadata, dict) else {}
    data = {}
    if selected_operation == "training":
        data = {key: privacy.memory_value(key, meta.get(key))
                for key in ("injuries", "equipment_available")}
        data["goal"] = privacy.token(user.goal, privacy.GOALS)
        data["fitness_level"] = privacy.token(user.fitness_level, privacy.LEVELS)
    elif selected_operation == "nutrition":
        data["dietary_restrictions"] = privacy.memory_value(
            "dietary_restrictions", meta.get("dietary_restrictions"))
    elif selected_operation == "recovery":
        rows = (WeeklyCheckIn.query.filter_by(user_id=user_id)
                .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
                .limit(4).all())
        data["recent_ratings"] = [privacy.numeric_fields(
            {key: getattr(row, key) for key in
             ("yogunluk", "fatigue", "uyku_kalitesi", "beslenme_uyumu")},
            ("yogunluk", "fatigue", "uyku_kalitesi", "beslenme_uyumu")) for row in rows]
    header = "[HAFTALIK CHECK-IN TRENDİ]" if selected_operation == "recovery" else "[KULLANICI PROFİLİ & HAFIZA]"
    return [header + "\n" + json.dumps(data, ensure_ascii=False)] if data else []


_CONTEXT_CATEGORIES = {
    "training": ("fitness_preferences", "health_constraints", "training_history", "workout_state"),
    "nutrition": ("fitness_preferences", "nutrition_activity", "nutrition_targets"),
    "recovery": ("recovery_ratings",),
    "body": ("body_metrics",),
    "goals": ("fitness_preferences",),
    "progress": ("training_history", "fitness_preferences", "health_constraints"),
    "chat": ("workout_state",),
}


def _read_context_fact(read, fallback):
    try:
        with db.session.begin_nested():
            return read()
    except Exception:
        current_app.logger.warning("[COACH] record projection unavailable")
        return fallback


def _nutrition_context(user_id, question, language, handoff, expanded):
    if not expanded:
        from app.coach_handoff import coach_handoff_context

        return [coach_handoff_context("nutrition-day", user_id, language)]

    from app.services.coach_nutrition_diary import nutrition_diary
    from app.services.nutrition_day_view import read_nutrition_target_and_hydration

    requested = (question or "").casefold()
    diary_requested = handoff == "nutrition-day" or re.search(
        r"\b(?:food (?:diary|log)|nutrition (?:diary|log)|meal log|i (?:ate|drank|eat|consumed|logged)|"
        r"my (?:meals?|calories|macros|intake|food log)|what (?:did|have) i (?:eat|eaten)|"
        r"(?:yemek|beslenme) günlüğ\w*|öğünlerim\w*|aldığım kalori\w*|"
        r"yedi(?:m|ğim|klerim|klerimi)|tükettim)\b", requested)
    today = app_today()
    parts = []
    today_relevant = True
    if diary_requested:
        yesterday = bool(re.search(r"\b(?:yesterday|dün)\b", requested))
        recent_days = bool(re.search(r"\b(?:last 7 days|past week|son 7 gün)\b", requested))
        days = [today - timedelta(days=offset) for offset in range(7)] if recent_days else [
            today - timedelta(days=int(yesterday))]
        include_today = handoff == "nutrition-day" or re.search(r"\b(?:today|bugün)\b", requested)
        if include_today and today not in days:
            days.insert(0, today)
        today_relevant = today in days
        diary = _read_context_fact(
            lambda: [nutrition_diary(user_id, day) for day in days],
            [{"day": day.isoformat(), "status": "unavailable", "entries": []} for day in days],
        )
        parts.append(
            "[NUTRITION DIARY] Values are amounts entered by the user. Unavailable totals are "
            "unknown. Never substitute a partial sum, assume zero, or invent an entry.\n"
            + json.dumps(diary))
    if today_relevant:
        target, hydration = read_nutrition_target_and_hydration(user_id, today)
        parts.append("[TODAY'S NUTRITION TARGET AND HYDRATION]\n" + json.dumps({
            "day": today.isoformat(),
            "target": {"status": target.state, "calories": target.value},
            "hydration": {"status": hydration.state, "glasses": hydration.amount},
        }))
    return parts


def fetch_coach_context(user_id, question="", language="tr", handoff=None):
    assert_principal(user_id)
    selected = privacy.context_operations(question, handoff)
    categories = {category for kind in selected for category in _CONTEXT_CATEGORIES[kind]}
    expanded = ai_consent.authorize_coach_read(user_id, *categories)
    user = db.session.get(User, user_id)
    if user is None:
        return ""
    if not expanded:
        if ai_consent.current_operation() is not None:
            return ""
        selected = {privacy.operation(question, handoff)}

    parts = []
    for kind in ("training", "nutrition", "recovery"):
        if kind in selected or (kind == "training" and "progress" in selected):
            parts.extend(fetch_profile_and_trends(user_id, kind))
    if "goals" in selected and not selected & {"training", "progress"}:
        parts.append(json.dumps({"goal": privacy.token(user.goal, privacy.GOALS)}))
    try:
        if "training" in selected:
            from app.services.training_history import build_training_history_summary
            summary = build_training_history_summary(user_id, weeks=1)
            parts.append(json.dumps({
                "period_days": 7, "total_workouts_completed": summary.total_sessions,
                "total_volume": summary.total_volume,
            }))
            if expanded and ("progress" in selected or re.search(
                    r"\b(?:history|recent|last|yesterday|logged|completed|sets?|reps?|"
                    r"trained|lifted|geçmiş\w*|dün|son|tamamla\w*|set\w*|tekrar\w*)\b",
                    (question or "").casefold())):
                from app.services.coach_workout_history import workout_history

                history = _read_context_fact(
                    lambda: workout_history(user_id, app_today()), '{"status":"unavailable"}')
                parts.append("[COMPLETED WORKOUTS: RECORDED SETS]\n" + history)
        if "nutrition" in selected:
            parts.extend(_nutrition_context(user_id, question, language, handoff, expanded))
        if "body" in selected:
            if expanded:
                from app.services.progress_summary import fetch_body_facts, summarize_body

                body = _read_context_fact(lambda: summarize_body(fetch_body_facts(user_id)), None)
                parts.append("[BODY PROGRESS]\n" + json.dumps(
                    {"status": "unavailable"} if body is None else {
                        "status": body.status, "weight_kg": body.current_weight_kg,
                        "target_weight_kg": body.target_weight_kg,
                        "recorded_weight_change_kg": body.weight_delta_kg,
                    }))
            else:
                parts.append(json.dumps(privacy.numeric_fields(
                    {"weight": user.weight, "target_weight": user.target_weight},
                    ("weight", "target_weight"))))
        if selected & {"chat", "training"}:
            # Generic Coach still needs the owner's canonical current state.
            # Use the snapshot interface, never its full UI/domain serializer.
            from app.services.workout_state.models import CANONICAL_PRIMARY_STATES
            state = resolve_workout_state(user_id)
            parts.append("[GÜNCEL ANTRENMAN DURUMU]\n" + json.dumps({
                "primary_state": privacy.token(state.primary_state, CANONICAL_PRIMARY_STATES),
                "completed_today": state.completed_today is True,
                "is_rest_day": state.is_rest_day is True,
            }))
        guidance = ("training" in selected and handoff != "nutrition-day"
                    and current_app.config.get("AI_ADAPTIVE_PLAN_CONTEXT", False))
        if "progress" in selected or handoff == "progress-insight" or (expanded and guidance):
            from app.coach_handoff import coach_handoff_context

            progress = coach_handoff_context("progress-insight", user_id, language)
            parts.append(progress or "[PROGRESS CONTEXT] Progress evidence is unavailable.")
        elif guidance:
            from app.services.adaptive_plan_context import build_coach_plan_context

            parts.append(build_coach_plan_context(user_id, language))
    except Exception:
        current_app.logger.warning("[COACH] selected context unavailable", exc_info=True)
    return "\n\n".join(part for part in parts if part)


# S1: fence jetonları harf-duyarsız kaçabiliyordu; zero-width/bidi karakterler
# (U+200B–200F, U+202A–202E, U+2066–2069, U+FEFF) görünmez talimat gizleyebilir.
_FRIEND_FENCE_OPEN_RE = re.compile(r"<<<\s*FRIEND_DATA", re.IGNORECASE)
_FRIEND_FENCE_CLOSE_RE = re.compile(r"FRIEND_DATA\s*>>>", re.IGNORECASE)
_INVISIBLE_CHARS_RE = re.compile(
    "[​-‏‪-‮⁦-⁩﻿]")


def neutralize_friend_content(text):
    """Üçüncü-taraf (arkadaş) metnini SALT VERİ fence'ine koymadan önce
    nötralize et: fence jetonlarını (harf-duyarsız) ve görünmez kontrol
    karakterlerini kaldır — fence kapatma/gizli talimat taşıyamasın."""
    text = _INVISIBLE_CHARS_RE.sub("", text)
    text = _FRIEND_FENCE_OPEN_RE.sub("", text)
    text = _FRIEND_FENCE_CLOSE_RE.sub("", text)
    return text
