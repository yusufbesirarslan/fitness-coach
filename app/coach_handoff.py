"""Progress → Coach contextual handoff (Progress V2 PR3).

"Review with AxisAI" on the Progress Axis Insight should land the user in
Coach with the insight already on the table, so they do not have to re-explain
their state, the signal, or the recommended move.

How, and why this way:

* The link carries ONE constant — ``/coach?review=progress-insight`` — and no
  user data. Every page loads analytics (``templates/_head.html``), which
  records the page location *including its query string*; an insight code in
  the URL would ship the user's training state to a third party. So the URL
  only says *which kind* of handoff this is.
* The Coach route re-derives the insight SERVER-SIDE for the signed-in user
  from the one canonical read model (``build_progress_insights``) — no second
  interpretation, no client-supplied facts, nothing spoofable about another
  user (the builder is owner-scoped by construction).
* The result is a localized, user-voice message rendered into the existing
  composer as a DRAFT. It is never auto-sent: rendering the page costs no model
  call, and the user decides whether to send, edit or discard it. There is no
  new persistence, no storage and no second Coach implementation.
* Fail-soft: any failure (read, unknown vocabulary) returns ``None`` and Coach
  opens exactly as it does without the parameter. A broken handoff must never
  break the Coach page.

The key tables are the Python twin of ``static/progress_presentation.js``
``AXIS_INSIGHT`` / ``AXIS_ACTION``; ``tests/test_progress_axis_insight.py``
proves the two agree, so Coach quotes the exact sentences Progress rendered.
"""
from flask import current_app

from app.i18n import t

# The one accepted value of ``?review=``. Anything else is ignored.
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


def coach_handoff_message(review, user_id):
    """The composer draft for ``?review=<review>``, or ``None``.

    ``None`` whenever there is nothing honest to pre-fill: no/unknown
    ``review`` value, an insight that cannot be built, or vocabulary this build
    does not map. Never raises.
    """
    if review != REVIEW_PROGRESS_INSIGHT:
        return None
    try:
        # Imported lazily: the Coach page must not pay for (or depend on) the
        # training read stack unless the handoff was actually requested.
        from app.services.progress_insights import build_progress_insights

        insight = build_progress_insights(user_id).insight
    except Exception as e:  # fail-soft by design — see module docstring
        current_app.logger.warning(
            "[COACH][HANDOFF] review=%s state=unavailable error_class=%s",
            REVIEW_PROGRESS_INSIGHT, type(e).__name__)
        return None
    if insight is None:
        return None
    insight_key = INSIGHT_KEYS.get(insight.code)
    action_key = ACTION_KEYS.get(insight.action_code)
    if not insight_key or not action_key:
        return None
    # The lead line and its "why" together: the same two sentences Progress
    # rendered as the interpretation.
    insight_text = "%s %s" % (t(insight_key), t(insight_key + "_why"))
    return t("coach.handoff_progress_insight",
             insight=insight_text, action=t(action_key))
