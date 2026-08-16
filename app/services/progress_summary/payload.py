"""JSON-safe projection of a ``ProgressSummary`` (Progress Redesign PR2).

Pure and total: a frozen value object in, a plain ``dict`` out. No DB, no Flask,
no clock — the route only has to ``jsonify`` the result. Same reasoning as
``app/services/weekly_program/payload.py``: the wire contract belongs to the
layer that owns the object, and the field list is written out by hand rather than
generated from ``dataclasses.asdict`` because this is a published surface —
adding a field to the model should be a deliberate decision here, not an
automatic leak into the response.

What is deliberately absent: database ids, the user id, ORM objects, localized
prose, and anything a provider produced. A consumer that wants words maps the
enums through its own catalog (``static/progress.js`` does exactly that).

``None`` stays ``None`` (JSON ``null``) and is never coerced to ``0`` — a missing
target weight and a target of zero are different claims, and only one of them is
ever true.
"""
from .models import CONTRACT_VERSION, ProgressSummary


def progress_summary_payload(summary: ProgressSummary) -> dict:
    """Canonical JSON-safe dict for one Progress summary."""
    window = summary.window
    body = summary.body
    performance = summary.performance
    consistency = summary.consistency
    return {
        "contract_version": CONTRACT_VERSION,
        "window": {
            "weeks": window.weeks,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": window.timezone,
        },
        # The classification, plus the canonical signal it was derived from, so a
        # reader can always trace the state back to the training authority.
        "trajectory": {
            "state": summary.trajectory.state,
            "reason": summary.trajectory.reason,
        },
        "body": {
            "status": body.status,
            "current_weight_kg": body.current_weight_kg,
            "weight_delta_kg": body.weight_delta_kg,
            "target_weight_kg": body.target_weight_kg,
            "distance_to_target_kg": body.distance_to_target_kg,
        },
        "performance": {
            "state": performance.state,
            "volume_trend": performance.volume_trend,
            "strength_trend": performance.strength_trend,
            "next_signal": performance.next_signal,
        },
        # Counts, not a rate: there is no canonical denominator for an adherence
        # percentage, so the client is given the numerator and the window instead.
        "consistency": {
            "state": consistency.state,
            "active_weeks": consistency.active_weeks,
            "analyzed_weeks": consistency.analyzed_weeks,
            "sessions": consistency.sessions,
        },
    }
