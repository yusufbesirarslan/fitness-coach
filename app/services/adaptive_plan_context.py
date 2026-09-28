"""Versioned read-only AdaptivePlan contract for AI Coach prompt consumers."""

import json

from flask import current_app

from app.i18n import t
from app.extensions import db
from app.services.training_planning import AdaptivePlan, build_adaptive_plan


SCHEMA_VERSION = 1
CONTEXT_HEADER = "[ADAPTIVE PLAN CONTRACT v1 - READ ONLY]"
CONSUMER_POLICY = (
    "Canonical read-only plan. Explain, personalize, motivate, educate, and "
    "present it; never recompute, reinterpret, or override decisions."
)


def serialize_adaptive_plan(plan: AdaptivePlan) -> str:
    """Pure AdaptivePlan -> canonical compact Version 1 JSON transformation."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "adaptive_plan",
        "plan": {
            "weeks": plan.weeks,
            "has_data": plan.has_data,
            "week_focus": plan.week_focus,
            "volume_action": plan.volume_action,
            "intensity_action": plan.intensity_action,
            "volume_delta_pct": plan.volume_delta_pct,
            "overload_ready": plan.overload_ready,
            "maintenance_recommended": plan.maintenance_recommended,
            "reason_codes": list(plan.reason_codes),
        },
        "progression": {
            "volume_trend": plan.progression.volume_trend,
            "strength_trend": plan.progression.strength_trend,
            "is_progressing": plan.progression.is_progressing,
            "is_plateau": plan.progression.is_plateau,
            "deload_due": plan.progression.deload_due,
            "load_consistency": plan.progression.load_consistency,
            "next_signal": plan.progression.next_signal,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_NEUTRAL_JSON = serialize_adaptive_plan(AdaptivePlan(weeks=0))


def _context_block(serialized: str) -> str:
    return f"{CONTEXT_HEADER}\n{CONSUMER_POLICY}\n{serialized}"


def _restore_session_usability() -> None:
    try:
        db.session.rollback()
    except Exception:
        try:
            db.session.remove()
        except Exception:
            pass


def build_adaptive_plan_context(user_id: int) -> str:
    """Build one complete enabled-path block; isolate application failures."""
    current_app.logger.debug("[COACH][ADAPTIVE_PLAN] planner enabled")
    try:
        plan = build_adaptive_plan(user_id)
    except Exception:
        _restore_session_usability()
        current_app.logger.debug("[COACH][ADAPTIVE_PLAN] planner fallback used")
        serialized = _NEUTRAL_JSON
    else:
        current_app.logger.debug(
            "[COACH][ADAPTIVE_PLAN] planner construction succeeded"
        )
        try:
            serialized = serialize_adaptive_plan(plan)
        except Exception:
            current_app.logger.debug("[COACH][ADAPTIVE_PLAN] planner fallback used")
            serialized = _NEUTRAL_JSON
    current_app.logger.debug("[COACH][ADAPTIVE_PLAN] serialization completed")
    return _context_block(serialized)


_COACH_NEUTRAL = {
    "en": ("[CURRENT TRAINING GUIDANCE]\nCurrent guidance is temporarily unavailable. "
           "Do not infer a new training adjustment from raw logs or check-ins."),
    "tr": ("[GÜNCEL ANTRENMAN ÖNERİSİ]\nGüncel öneri şu anda alınamıyor. "
           "Ham kayıtlardan veya check-in verilerinden yeni bir antrenman ayarı çıkarma."),
}


def planned_volume_copy(action, language: str = "tr") -> str:
    """Present the canonical adjustment without selecting or sizing it."""
    if action.week_focus not in ("overload", "deload"):
        return ""
    percent = action.volume_delta_pct * 100
    if not (-100 <= percent <= 100):
        raise ValueError("Invalid canonical volume adjustment")
    return (f"Planned weekly volume change: {percent:+g}%." if language == "en" else
            f"Planlanan haftalık hacim değişimi: %{percent:+g}.")


def project_coach_plan(plan: AdaptivePlan, language: str = "tr") -> str:
    """Present the existing planner decision using Progress's public copy.

    This does not select a new action or fetch a second report. Unknown vocabulary
    fails neutral instead of exposing a machine code or guessing a decision.
    """
    from app.coach_handoff import ACTION_KEYS, INSIGHT_KEYS
    from app.services.progress_insights.analysis import (
        INSIGHT_BY_WEEK_FOCUS, NEXT_MOVE_BY_WEEK_FOCUS,
    )

    locale = language if language in _COACH_NEUTRAL else "tr"
    try:
        insight_code = INSIGHT_BY_WEEK_FOCUS[plan.week_focus]
        action_code = NEXT_MOVE_BY_WEEK_FOCUS[plan.week_focus]
        insight_key = INSIGHT_KEYS[insight_code]
        action_key = ACTION_KEYS[action_code]
    except (KeyError, TypeError):
        return _COACH_NEUTRAL[locale]

    lines = [
        "[CURRENT TRAINING GUIDANCE]" if locale == "en" else "[GÜNCEL ANTRENMAN ÖNERİSİ]",
        t(insight_key, locale=locale),
        t(action_key, locale=locale),
    ]
    try:
        adjustment = planned_volume_copy(plan, locale)
    except ValueError:
        return _COACH_NEUTRAL[locale]
    if adjustment:
        lines.append(adjustment)
    return "\n".join(lines)


def build_coach_plan_context(user_id: int, language: str = "tr") -> str:
    """Build one Coach-safe projection from one canonical planner read."""
    locale = language if language in _COACH_NEUTRAL else "tr"
    try:
        return project_coach_plan(build_adaptive_plan(user_id), locale)
    except Exception:
        _restore_session_usability()
        current_app.logger.debug("[COACH][ADAPTIVE_PLAN] public projection fallback used")
        return _COACH_NEUTRAL[locale]
