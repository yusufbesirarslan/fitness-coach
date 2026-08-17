"""JSON-safe projection of a ``ProgressInsights`` (Progress Redesign PR3).

Pure and total: a frozen value object in, a plain ``dict`` out. No DB, no Flask,
no clock — the route only has to ``jsonify`` the result. Same reasoning as
``progress_summary/payload.py``: the wire contract belongs to the layer that
owns the object, and the field list is written out by hand rather than generated
from ``dataclasses.asdict``, because this is a published surface — adding a
field to the model should be a deliberate decision here, not an automatic leak
into the response.

What is deliberately absent: database ids, the user id, ORM objects, localized
prose, and anything a provider produced. There is no provider in this package at
all. A consumer that wants words maps ``code`` through its own catalog
(``static/progress_insights.js`` does exactly that).

All three slots publish the **same** five keys. A uniform shape means a client
reads one slot renderer, and an absent value is an explicit ``null`` rather than
a missing key it has to probe for.
"""
from .models import CONTRACT_VERSION, InsightSlot, ProgressInsights


def _slot_payload(slot: InsightSlot) -> dict:
    """One slot as five explicit keys; ``None`` stays ``None``, never ``{}``.

    An empty ``evidence`` object and a null one are different claims — the first
    says "here are the facts: none", the second says "this slot names no facts
    because it names no insight" — and only the second is ever true here.
    """
    action = slot.action
    return {
        "status": slot.status,
        "code": slot.code,
        "domain": slot.domain,
        "evidence": None if slot.evidence is None else dict(slot.evidence),
        "action": None if action is None else {
            "week_focus": action.week_focus,
            "volume_action": action.volume_action,
            "intensity_action": action.intensity_action,
            # The planner's signed fraction, unrounded and unconverted. The
            # client formats it for display; the server does not decide how it
            # reads, and nothing here turns 0.05 into a "5" that would then be
            # ambiguous between a percent and a fraction.
            "volume_delta_pct": action.volume_delta_pct,
        },
    }


def progress_insights_payload(insights: ProgressInsights) -> dict:
    """Canonical JSON-safe dict for one Axis Insights read model."""
    window = insights.window
    return {
        "contract_version": CONTRACT_VERSION,
        # The same window ``GET /api/progress/summary`` publishes, built from the
        # same call on the same resolved day — the two surfaces cannot disagree
        # about which period they analysed.
        "window": {
            "weeks": window.weeks,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": window.timezone,
        },
        "working": _slot_payload(insights.working),
        "watch": _slot_payload(insights.watch),
        "next_move": _slot_payload(insights.next_move),
    }
