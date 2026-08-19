"""JSON-safe projection of a ``ProgressHistory`` (Progress Redesign PR5).

Pure and total: a frozen value object in, a plain ``dict`` out. No DB, no Flask,
no clock. The field list is written by hand so adding a model field is a
deliberate wire decision, not an automatic leak.

Deliberately absent: database ids, user id, ORM objects, localized prose,
``coach_feedback``, current profile weight, current target, Axis Insights,
planner actions, and anything a provider produced.
"""
from app.timeutil import to_app_tz

from .models import CONTRACT_VERSION, HistoryEntry, ProgressHistory


def _iso_checked_in_at(value):
    """Offset-aware ISO 8601 in the application timezone (Europe/Istanbul)."""
    local = to_app_tz(value)
    return local.isoformat() if local is not None else None


def _entry_payload(entry: HistoryEntry) -> dict:
    window = entry.window
    return {
        "checked_in_at": _iso_checked_in_at(entry.checked_in_at),
        "analysis_day": entry.analysis_day.isoformat(),
        "window": {
            "weeks": window.weeks,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": window.timezone,
        },
        "trajectory": {
            "state": entry.trajectory.state,
            "reason": entry.trajectory.reason,
        },
        "performance": {
            "state": entry.performance.state,
            "volume_trend": entry.performance.volume_trend,
        },
        "consistency": {
            "state": entry.consistency.state,
            "sessions": entry.consistency.sessions,
            "active_weeks": entry.consistency.active_weeks,
            "analyzed_weeks": entry.consistency.analyzed_weeks,
        },
        "body": {
            "weight_kg": entry.weight_kg,
            "weight_delta_kg": entry.weight_delta_kg,
        },
    }


def progress_history_payload(history: ProgressHistory) -> dict:
    """Canonical JSON-safe dict for one Progress History read model."""
    return {
        "contract_version": CONTRACT_VERSION,
        "state": history.state,
        "entries": [_entry_payload(entry) for entry in history.entries],
        "has_more": history.has_more,
    }
