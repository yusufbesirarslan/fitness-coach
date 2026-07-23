"""Canonical workout-state contract (Sprint 7 PR1).

The single owner of *current* workout-state resolution and action eligibility.
It converts existing trusted persistence + service data (TrainingPlan schedule,
today's PumpCheck completion, WorkoutLog execution evidence via the
training-history foundation) into one deterministic, serializable snapshot.

Boundaries (see docs/WORKOUT_STATE.md):
  * Read-only — no writes, no flush/commit, no data repair, no XP/challenge/
    notification side effects, no AI/external calls.
  * Consumes established contracts; copies no other service's heuristics.
  * Timezone via ``app.timeutil`` only (no second date helper).

Layering: ``queries`` (impure reads) → ``resolver`` (pure classification); this
module orchestrates them and owns fail-safe error handling + safe logging.
"""
from datetime import date
from typing import Optional

from flask import current_app

from app.observability import current_request_id
from app.timeutil import app_today

from .models import (
    ACTION_BLOCKED,
    ANOMALY_RESOLUTION_ERROR,
    CONTRACT_VERSION,
    EXEC_NONE,
    PRIMARY_NEEDS_ATTENTION,
    REL_INDETERMINATE,
    SCHEDULE_UNAVAILABLE,
    WorkoutStateInputs,
    WorkoutStateSnapshot,
)
from .queries import load_inputs
from .resolver import resolve

__all__ = [
    "resolve_workout_state",
    "resolve_from_inputs",
    "WorkoutStateSnapshot",
    "WorkoutStateInputs",
    "CONTRACT_VERSION",
]


def resolve_from_inputs(inputs: WorkoutStateInputs) -> WorkoutStateSnapshot:
    """Pure re-export of the resolver for callers that already hold inputs."""
    return resolve(inputs)


def resolve_workout_state(
    user_id: int, *, today: Optional[date] = None
) -> WorkoutStateSnapshot:
    """Resolve ``user_id``'s current workout state for Istanbul day ``today``.

    ``today`` defaults to ``app_today()`` (Istanbul); pass an explicit day for
    hermetic tests. Never raises for domain conditions; an unexpected read
    failure fails **safe** (a blocked ``needs_attention`` snapshot) rather than a
    misleading rest/completed state, and logs only safe operational metadata.
    """
    day = today or app_today()
    try:
        inputs = load_inputs(user_id, day)
    except Exception as exc:  # noqa: BLE001 — fail safe, never leak to the client
        _log_anomaly(user_id, ANOMALY_RESOLUTION_ERROR, type(exc).__name__)
        return _safe_snapshot(day)

    snapshot = resolve(inputs)
    if snapshot.anomaly:
        _log_anomaly(user_id, snapshot.anomaly, None)
    return snapshot


def _safe_snapshot(day: date) -> WorkoutStateSnapshot:
    """The deterministic fail-safe state when inputs cannot be loaded."""
    return WorkoutStateSnapshot(
        today=day,
        schedule_state=SCHEDULE_UNAVAILABLE,
        execution_state=EXEC_NONE,
        plan_relationship=REL_INDETERMINATE,
        action=ACTION_BLOCKED,
        primary_state=PRIMARY_NEEDS_ATTENTION,
        completed_today=False,
        is_rest_day=False,
        stale_previous_workout=False,
        anomaly=ANOMALY_RESOLUTION_ERROR,
    )


def _log_anomaly(user_id: int, category: str, detail: Optional[str]) -> None:
    """Emit only safe operational metadata (no health data, no payloads).

    Best-effort: logging must never turn a read into a failure.
    """
    try:
        current_app.logger.warning(
            "[WORKOUT_STATE] anomaly rid=%s user_id=%s category=%s detail=%s",
            current_request_id(), user_id, category, detail or "-",
        )
    except Exception:  # noqa: BLE001
        pass
