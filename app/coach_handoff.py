"""Progress to Coach handoff. Only the fixed kind crosses the client boundary."""
from flask import current_app

from app.i18n import t

REVIEW_PROGRESS_INSIGHT = "progress-insight"

INSIGHT_KEYS = {
    "baseline": "progress.axis_insight_baseline",
    "consistency_gaps": "progress.axis_insight_consistency_gaps",
    "deload_due": "progress.axis_insight_deload_due",
    "stalled": "progress.axis_insight_stalled",
    "ready_to_progress": "progress.axis_insight_ready_to_progress",
    "holding_steady": "progress.axis_insight_holding_steady",
    "steady_with_dip": "progress.axis_insight_steady_with_dip",
}
ACTION_KEYS = {
    "build_baseline": "progress.axis_action_build_baseline",
    "prioritize_consistency": "progress.axis_action_prioritize_consistency",
    "deload": "progress.axis_action_deload",
    "maintain_and_consolidate": "progress.axis_action_maintain_and_consolidate",
    "progress_training": "progress.axis_action_progress_training",
    "maintain_current_training": "progress.axis_action_maintain_current_training",
}


def _unavailable(exc):
    from app.extensions import db
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.warning(
        "[COACH][HANDOFF] state=unavailable error_class=%s", type(exc).__name__)


def _public_insight(user_id, language="tr", include_evidence=False):
    """Re-derive owner-scoped canonical facts and project only public copy."""
    from app.services.progress_insights import EVIDENCE_CODES, build_progress_insights

    insight = build_progress_insights(user_id).insight
    if insight is None:
        return None
    insight_key = INSIGHT_KEYS.get(insight.code)
    action_key = ACTION_KEYS.get(insight.action_code)
    if not insight_key or not action_key:
        return None
    lines = [t(insight_key, locale=language),
             t(insight_key + "_why", locale=language),
             t(action_key, locale=language)]
    if any(value.startswith("progress.") or "{" in value for value in lines):
        return None
    if include_evidence:
        from app.services.adaptive_plan_context import planned_volume_copy

        if insight.action is not None:
            adjustment = planned_volume_copy(insight.action, language)
            if adjustment:
                lines.append(adjustment)
        for item in insight.evidence:
            if item.code not in EVIDENCE_CODES:
                return None
            params = item.params or {}
            if not isinstance(params, dict) or any(
                    not isinstance(v, int) or isinstance(v, bool) or v < 0
                    for v in params.values()):
                return None
            rendered = t("progress.axis_evidence_" + item.code,
                         locale=language, **params)
            if rendered.startswith("progress.") or "{" in rendered:
                return None
            lines.append(rendered)
    return lines


def coach_handoff_message(review, user_id, language="tr"):
    """Page preview and short draft, or None when the handoff is unavailable."""
    if review != REVIEW_PROGRESS_INSIGHT:
        return None
    try:
        lines = _public_insight(user_id, language)
    except Exception as exc:
        _unavailable(exc)
        return None
    if lines is None:
        return None
    return {"preview": lines[0], "action": lines[2],
            "draft": t("coach.handoff_draft", locale=language)}


def coach_handoff_context(review, user_id, language="tr"):
    """Send-time context for the model. Never stored as user speech."""
    if review != REVIEW_PROGRESS_INSIGHT:
        return ""
    try:
        lines = _public_insight(user_id, language, include_evidence=True)
    except Exception as exc:
        _unavailable(exc)
        return ""
    if lines is None:
        return ""
    header = ("[CURRENT TRAINING GUIDANCE]" if language == "en" else
              "[GÜNCEL ANTRENMAN ÖNERİSİ]")
    return header + "\n[PROGRESS CONTEXT]\n" + "\n".join(lines)
