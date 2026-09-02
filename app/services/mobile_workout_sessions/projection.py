"""The single bounded public projection of a native workout session.

Every session write endpoint returns this one shape, so a native client learns
one contract instead of six. It carries exactly what native execution needs to
render, resume and safely retry: opaque identity, the canonical workout the
session belongs to, the server-authored lifecycle classification, the optimistic
revision, and the durable progress snapshot.

It deliberately exposes NO internal database id, no private event payload, no
provider metadata and no unrelated user state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .checkpoint import load_snapshot


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Server-authored timestamps only, always rendered as UTC ``Z`` instants.

    The stored columns are naive UTC (``datetime.utcnow``), matching the rest of
    the persisted lifecycle. A client clock is never a source of any of these.
    """
    if not isinstance(value, datetime):
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def project_session(row, view) -> dict:
    """Project one owned session row plus its canonical classification.

    ``row`` is the persisted ``WorkoutSession``; ``view`` is the canonical
    ``SessionView`` produced by the session authority (relationship / staleness /
    resumability are ITS decisions, never recomputed here).
    """
    return {
        "session_ref": row.public_id,
        "workout_ref": row.workout_ref,
        "plan_lineage": row.plan_lineage_id,
        "mutation_version": row.plan_mutation_version,
        "status": row.status,
        "workout_date": row.workout_date,
        "source": row.source,
        "relationship": view.relationship,
        "stale_reason": view.stale_reason,
        "resumable": view.resumable,
        "revision": int(row.checkpoint_revision or 0),
        "started_at": _iso(row.started_at),
        "last_activity_at": _iso(row.last_activity_at),
        "checkpoint_at": _iso(row.checkpoint_at),
        "completed_at": _iso(row.completed_at),
        "abandoned_at": _iso(row.abandoned_at),
        "terminal_reason": row.terminal_reason,
        "checkpoint": load_snapshot(row.checkpoint_data),
    }


def project_completion(completion) -> Optional[dict]:
    """Bounded reward projection for a completion outcome.

    ``None`` when the command produced no completion result. On a replay the
    canonical result carries no reward fields, and this reports exactly that
    rather than fabricating a second award.
    """
    if completion is None:
        return None
    return {
        "outcome": completion.outcome.value,
        "xp_awarded": completion.xp_awarded,
        "new_total": completion.new_total,
        "level": completion.level,
        "title": completion.title,
    }
