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
    ANOMALY_SESSION_READ_ERROR,
    CONTRACT_VERSION,
    CONTRACT_VERSION_SESSIONS,
    EXEC_NONE,
    PRIMARY_NEEDS_ATTENTION,
    REL_INDETERMINATE,
    SCHEDULE_UNAVAILABLE,
    ACTIVE_SESSION_FACTS_NONE,
    WorkoutStateInputs,
    WorkoutStateSnapshot,
)
from .queries import PLAN_NOT_PROVIDED, load_inputs, load_session_facts
from .resolver import enrich_with_session, resolve

# Config flag (single canonical owner in app/config.py) gating the persisted
# workout-session lifecycle. OFF ⇒ the resolver returns the exact PR1 contract;
# ON ⇒ the session-aware v2 contract. Read defensively so a missing key or a
# broken app context can never turn a read into a failure.
_SESSIONS_FLAG = "FITX_WORKOUT_SESSIONS_ENABLED"

__all__ = [
    "resolve_workout_state",
    "resolve_from_inputs",
    "WorkoutStateSnapshot",
    "WorkoutStateInputs",
    "CONTRACT_VERSION",
    "CONTRACT_VERSION_SESSIONS",
    "WorkoutStateReadError",
]


class WorkoutStateReadError(RuntimeError):
    """Unexpected persistence failure requested to propagate fail-closed."""


def resolve_from_inputs(inputs: WorkoutStateInputs) -> WorkoutStateSnapshot:
    """Pure re-export of the PR1 resolver for callers that already hold inputs.

    Always the v1 base snapshot (no session enrichment) — the flag-conditional v2
    projection lives in :func:`resolve_workout_state`, which owns the session read.
    """
    return resolve(inputs)


def resolve_workout_state(
    user_id: int, *, today: Optional[date] = None, plan=PLAN_NOT_PROVIDED,
    sessions_enabled: Optional[bool] = None, strict_reads: bool = False,
) -> WorkoutStateSnapshot:
    """Resolve ``user_id``'s current workout state for Istanbul day ``today``.

    ``today`` defaults to ``app_today()`` (Istanbul); pass an explicit day for
    hermetic tests. Never raises for domain conditions; an unexpected read
    failure fails **safe** (a blocked ``needs_attention`` snapshot) rather than a
    misleading rest/completed state, and logs only safe operational metadata.

    Contract is flag-conditional (Sprint 7 PR3): with ``FITX_WORKOUT_SESSIONS_ENABLED``
    OFF the returned snapshot is the exact PR1 ``contract_version=1`` contract; ON,
    it is enriched (read-only) to the session-aware ``contract_version=2``.
    """
    day = today or app_today()
    try:
        inputs = load_inputs(user_id, day, plan=plan)
    except Exception as exc:  # noqa: BLE001 — fail safe, never leak to the client
        _log_anomaly(user_id, ANOMALY_RESOLUTION_ERROR, type(exc).__name__)
        if strict_reads:
            raise WorkoutStateReadError("workout-state input read failed") from exc
        return _maybe_enrich(
            user_id, day, _safe_snapshot(day), sessions_enabled, strict_reads)

    snapshot = resolve(inputs)
    if snapshot.anomaly:
        _log_anomaly(user_id, snapshot.anomaly, None)
    return _maybe_enrich(user_id, day, snapshot, sessions_enabled, strict_reads)


def _sessions_enabled() -> bool:
    try:
        return bool(current_app.config.get(_SESSIONS_FLAG, False))
    except Exception:  # noqa: BLE001 — no app context / missing config → OFF
        return False


def _maybe_enrich(
    user_id: int, day: date, base: WorkoutStateSnapshot,
    sessions_enabled: Optional[bool] = None, strict_reads: bool = False,
) -> WorkoutStateSnapshot:
    """Flag OFF ⇒ return the PR1 base untouched (byte-identical v1). Flag ON ⇒
    load session truth read-only and return the v2 enrichment; a session-read
    failure still yields a valid v2 snapshot with an empty session dimension
    (fail-safe) so the contract mode stays consistent with the flag."""
    enabled = _sessions_enabled() if sessions_enabled is None else sessions_enabled
    if not enabled:
        return base
    try:
        facts = load_session_facts(user_id, day)
    except Exception as exc:  # noqa: BLE001 — never break the read on a session error
        _log_anomaly(user_id, ANOMALY_SESSION_READ_ERROR, type(exc).__name__)
        if strict_reads:
            raise WorkoutStateReadError("workout-session read failed") from exc
        facts = ACTIVE_SESSION_FACTS_NONE
    enriched = enrich_with_session(base, facts)
    if enriched.anomaly and enriched.anomaly != base.anomaly:
        _log_anomaly(user_id, enriched.anomaly, None)
    return enriched


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
