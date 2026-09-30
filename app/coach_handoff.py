"""Coach handoffs. Only a fixed, allowlisted kind crosses the client boundary.

Two kinds exist:

* ``progress-insight`` (Progress V2 PR3) — the Axis Insight continuation.
* ``nutrition-day`` (NUTR-PR6) — "Review with AxisAI" from Nutrition Today.

For both, the browser sends ONLY the marker. The authenticated owner id is
passed separately by the route, and every fact is re-derived here from the
canonical read model — at page render for the bounded preview, and again at
send time for the model context. No browser-supplied value is ever context.
"""
import math

from flask import current_app

from app.i18n import t

REVIEW_PROGRESS_INSIGHT = "progress-insight"
REVIEW_NUTRITION_DAY = "nutrition-day"
HANDOFF_KINDS = frozenset((REVIEW_PROGRESS_INSIGHT, REVIEW_NUTRITION_DAY))

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


def handoff_marker(value):
    """The allowlisted kind, or None. Anything else (unknown, forged, a list,
    a number, a dict) is ignored — it never reaches the pipeline."""
    if isinstance(value, str) and value in HANDOFF_KINDS:
        return value
    return None


def replaces_plan_projection(kind):
    """Progress context IS the plan projection (one canonical build, no second
    report). Nutrition context is additive: the normal plan projection stays."""
    return kind == REVIEW_PROGRESS_INSIGHT


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


# ── NUTR-PR6: nutrition-day ─────────────────────────────────────────────


def _whole(value):
    """The browser's Math.round (floor(x + 0.5)); never invents a number."""
    return math.floor(value + 0.5)


def _nutrition_view(user_id):
    """The canonical day view, or None when the intake read — the one fact a
    nutrition review cannot do without — is not safely known."""
    from app.services.nutrition_day_view import READABLE, build_nutrition_day_view

    view = build_nutrition_day_view(user_id)
    if view.intake.state not in READABLE:
        return None
    return view


def _nutrition_preview_lines(view, language):
    """At most two short, factual lines. No verdict, no gap, no advice."""
    from app.services.nutrition_day_view import AVAILABLE, EMPTY

    if view.intake.state == AVAILABLE:
        intake = t("coach.handoff_nutrition_intake", locale=language,
                   meals=view.intake.meal_count,
                   kcal=_whole(view.intake.totals["calories"]))
    else:
        intake = t("coach.handoff_nutrition_intake_empty", locale=language)
    if view.target.state == AVAILABLE:
        target = t("coach.handoff_nutrition_target", locale=language,
                   kcal=_whole(view.target.value))
    elif view.target.state == EMPTY:
        target = t("coach.handoff_nutrition_target_absent", locale=language)
    else:
        target = t("coach.handoff_nutrition_target_unknown", locale=language)
    lines = [intake, target]
    if any(value.startswith("coach.") or "{" in value for value in lines):
        return None
    return lines


def _nutrition_context_text(view, language):
    """Typed, factual model context. Unknown stays unknown; nothing is scored."""
    from app.services.nutrition_day_view import AVAILABLE, EMPTY, INVALID

    def state_word(state):
        return {EMPTY: "none", INVALID: "unreadable"}.get(state, "unknown (read failed)")

    if view.target.state == AVAILABLE:
        target = f"{_whole(view.target.value)} kcal per day"
    elif view.target.state == EMPTY:
        target = "not set"
    else:
        target = state_word(view.target.state)
    totals = view.intake.totals
    intake = (f"{view.intake.meal_count} meal(s) logged; "
              f"{_whole(totals['calories'])} kcal, protein {_whole(totals['protein'])} g, "
              f"carbs {_whole(totals['carbs'])} g, fat {_whole(totals['fat'])} g"
              if view.intake.state == AVAILABLE else "no meals logged yet (measured 0)")
    if view.hydration.state in (AVAILABLE, EMPTY):
        hydration = f"{view.hydration.amount} glass(es) of water"
    else:
        hydration = state_word(view.hydration.state)
    if view.plan.state == AVAILABLE:
        plan = f"saved plan with {view.plan.summary.planned_meal_count} planned meal(s)"
    elif view.plan.state == EMPTY:
        plan = "no saved plan"
    else:
        plan = state_word(view.plan.state)
    header = ("[TODAY'S NUTRITION]" if language == "en" else "[BUGÜNKÜ BESLENME]")
    return "\n".join((
        header,
        "[NUTRITION CONTEXT] The user opened this review from Nutrition. Facts "
        "below were re-read from the user's own records now. \"unknown\" means "
        "the value could not be read — it is NOT zero. Do not compute an "
        "adherence percentage or score, and do not invent missing values.",
        f"- day: {view.day.isoformat()}",
        f"- daily target: {target}",
        f"- logged today: {intake}",
        f"- hydration today: {hydration}",
        f"- nutrition plan: {plan}",
    ))


def _nutrition_message(user_id, language):
    view = _nutrition_view(user_id)
    if view is None:
        return None
    lines = _nutrition_preview_lines(view, language)
    if lines is None:
        return None
    return {"kind": REVIEW_NUTRITION_DAY,
            "label": t("coach.handoff_from_nutrition", locale=language),
            "dismiss": t("coach.handoff_nutrition_dismiss", locale=language),
            "lines": lines,
            "draft": t("coach.handoff_nutrition_draft", locale=language)}


def _progress_message(user_id, language):
    lines = _public_insight(user_id, language)
    if lines is None:
        return None
    return {"kind": REVIEW_PROGRESS_INSIGHT,
            "label": t("coach.handoff_from_progress", locale=language),
            "dismiss": t("coach.handoff_dismiss", locale=language),
            "lines": [lines[0], lines[2]],
            "preview": lines[0], "action": lines[2],
            "draft": t("coach.handoff_draft", locale=language)}


def coach_handoff_message(review, user_id, language="tr"):
    """Page preview and short draft, or None when the handoff is unavailable.

    Rendering this never sends anything and never calls a model."""
    kind = handoff_marker(review)
    if kind is None:
        return None
    try:
        if kind == REVIEW_NUTRITION_DAY:
            return _nutrition_message(user_id, language)
        return _progress_message(user_id, language)
    except Exception as exc:
        _unavailable(exc)
        return None


def coach_handoff_context(review, user_id, language="tr"):
    """Send-time context for the model. Never stored as user speech.

    Re-derived now for the authenticated owner; an unknown marker, a failed
    derivation or an unknown intake yields "" (no context is claimed)."""
    kind = handoff_marker(review)
    if kind is None:
        return ""
    try:
        if kind == REVIEW_NUTRITION_DAY:
            view = _nutrition_view(user_id)
            return "" if view is None else _nutrition_context_text(view, language)
        lines = _public_insight(user_id, language, include_evidence=True)
    except Exception as exc:
        _unavailable(exc)
        return ""
    if lines is None:
        return ""
    header = ("[CURRENT TRAINING GUIDANCE]" if language == "en" else
              "[GÜNCEL ANTRENMAN ÖNERİSİ]")
    return header + "\n[PROGRESS CONTEXT]\n" + "\n".join(lines)
