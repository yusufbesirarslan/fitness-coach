"""Operation-specific, owner-scoped Coach context (PA06).

Social queries and arbitrary stored prose are intentionally absent. Domain/UI
serializers remain available to non-AI callers.
"""
import json
import re
from flask import current_app, g
from app.extensions import db
from app.models import User, WeeklyCheckIn
from app.services import coach_privacy as privacy
from app.services.workout_state import resolve_workout_state


def assert_principal(user_id):
    """Savunma derinliği: in-process koç/MCP araçları yalnızca kimliği doğrulanmış
    kullanıcı için çalışmalı. user_id ASLA LLM'den gelmez; yine de bir istek
    bağlamında çağrıldıysak current_user ile eşleştiğini doğrula (gelecekteki bir
    yanlış kullanım çapraz-kullanıcı okuma/yazmaya dönüşmesin)."""
    from flask import has_request_context
    from flask_login import current_user
    if has_request_context():
        principal = getattr(g, "mobile_user", None)
        if principal is None and getattr(current_user, "is_authenticated", False):
            principal = current_user
        if principal is not None and principal.id != user_id:
            raise PermissionError("user_id does not match the authenticated owner")


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


def fetch_coach_context(user_id, question="", language="tr", handoff=None):
    assert_principal(user_id)
    if db.session.get(User, user_id) is None:
        return ""
    selected = privacy.operation(question, handoff)
    parts = fetch_profile_and_trends(user_id, selected)
    try:
        if selected == "training":
            from app.services.training_history import build_training_history_summary
            from app.services.workout_state.models import CANONICAL_PRIMARY_STATES
            summary = build_training_history_summary(user_id, weeks=1)
            parts.append(json.dumps({
                "period_days": 7, "total_workouts_completed": summary.total_sessions,
                "total_volume": summary.total_volume,
                "volume_trend": privacy.token(summary.volume_trend, ("up", "flat", "down")),
            }))
            state = resolve_workout_state(user_id)
            parts.append("[GÜNCEL ANTRENMAN DURUMU]\n" + json.dumps({
                "primary_state": privacy.token(state.primary_state, CANONICAL_PRIMARY_STATES),
                "completed_today": state.completed_today is True,
                "is_rest_day": state.is_rest_day is True,
            }))
        elif selected == "nutrition" and handoff != "nutrition-day":
            # Reuse the already closed owned daily-facts projection.
            from app.coach_handoff import coach_handoff_context
            parts.append(coach_handoff_context("nutrition-day", user_id, language))
        elif selected == "body":
            user = db.session.get(User, user_id)
            parts.append(json.dumps(privacy.numeric_fields(
                {"weight": user.weight, "target_weight": user.target_weight},
                ("weight", "target_weight"))))
        if handoff in ("progress-insight", "nutrition-day"):
            from app.coach_handoff import coach_handoff_context
            parts.append(coach_handoff_context(handoff, user_id, language))
        elif selected == "training" and current_app.config.get("AI_ADAPTIVE_PLAN_CONTEXT", False):
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
