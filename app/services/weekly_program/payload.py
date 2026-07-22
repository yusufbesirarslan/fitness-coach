"""JSON-safe projection of a ``WeeklyProgramRecommendation`` (Sprint 6 PR5).

Pure and total: a frozen value object in, a plain ``dict`` out. No DB, no Flask, no
clock — the route layer only has to call ``jsonify`` on the result.

**Why the projection lives here and not in the route.** The runtime contract belongs
to the layer that owns the object, so the route stays thin and a second, drifting
shape cannot appear next to this one. The field list below is written out explicitly
rather than generated from ``dataclasses.asdict``: this is a published API surface, so
adding a field to the model should be a deliberate decision here, not an automatic
leak into the response. ``tests/test_weekly_program_route.py`` pins both directions —
every model field is exposed, and nothing else is.

Distinct from the PR4 prompt contract (``app/services/adaptive_plan_context.py``),
which owns the only ``AdaptivePlan`` -> JSON serialization. That one serializes the
*plan* for the coach prompt; this one serializes the *recommendation* for a read-only
HTTP consumer. Different inputs, different audiences, no overlap — and this module
never imports ``AdaptivePlan``, so the single-owner guard is untouched.

Two conversions, both mechanical: ``date`` -> ISO ``YYYY-MM-DD`` string, and tuples ->
lists so the payload compares equal to what a client parses back out of the response.
``None`` stays ``None`` (JSON ``null``) — never coerced to ``0``, which would read as
"train nothing this week" instead of "not enough data to say".
"""
from .models import WeeklyProgramRecommendation


def weekly_program_payload(recommendation: WeeklyProgramRecommendation) -> dict:
    """Canonical JSON-safe dict for one weekly-program recommendation.

    Field-for-field with the model: no renames, no added decision fields, no derived
    values. Anything a consumer reads here it could have read off the object itself.
    """
    baseline_week_start = recommendation.baseline_week_start
    return {
        # Decisions — echoed from AdaptivePlan by the service layer, echoed again here.
        "weeks": recommendation.weeks,
        "has_data": recommendation.has_data,
        "week_focus": recommendation.week_focus,
        "volume_action": recommendation.volume_action,
        "intensity_action": recommendation.intensity_action,
        "volume_delta_pct": recommendation.volume_delta_pct,
        "overload_ready": recommendation.overload_ready,
        "maintenance_recommended": recommendation.maintenance_recommended,
        # Observed + derived volume. Both null together when no positive volume exists.
        "baseline_week_start": (
            None if baseline_week_start is None else baseline_week_start.isoformat()
        ),
        "baseline_weekly_volume": recommendation.baseline_weekly_volume,
        "target_weekly_volume": recommendation.target_weekly_volume,
        # Explanation vocabulary — ordered, locale-neutral keys; no user-facing text.
        "reason_codes": list(recommendation.reason_codes),
        "explanation_keys": list(recommendation.explanation_keys),
        # Capabilities AdaptivePlan does not model, so a client can render an explicit
        # "unsupported" state instead of receiving an invented number.
        "unsupported": list(recommendation.unsupported),
    }
