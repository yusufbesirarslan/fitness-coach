"""Versioned read-only AdaptivePlan contract for AI Coach prompt consumers."""

import json

from flask import current_app

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
