"""Pure deterministic training facts over canonical completed execution.

Input is a sequence of :class:`StoredSession` raw columns read by
``queries.py``; nothing here touches the database, Flask, a provider, the
current plan, ``WorkoutLog`` or Pump Check. Every fact that could be mistaken
for complete history carries its coverage, and a value that was not observed is
``None`` -- never zero.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

from app.services.workout_session.checkpoint import load_snapshot, parse_stored_exercises
from app.services.workout_session.context import project_context
from app.services.workout_session.errors import InvalidSessionRequest
from app.services.workout_session.prescription import project as project_prescription
from app.timeutil import app_date_of

from .models import (
    AVAILABILITY_INCONSISTENT,
    AVAILABILITY_OBSERVED,
    AVAILABILITY_UNAVAILABLE,
    EXPOSURE_WEEKS,
    HISTORY_DAYS,
    LOGGING_INTERVAL_METHOD,
    MAX_RECENT_SESSIONS,
    RIR_ORDER,
    Coverage,
    ExerciseExecution,
    ExposureFact,
    FrequencyFact,
    IntervalFact,
    Occurrence,
    RecentPerformance,
    RirFact,
    SessionExecution,
    SetObservation,
    StoredSession,
    VolumeFact,
)


# ── Reading one session ─────────────────────────────────────────────────────
def _parse_day(raw) -> Optional[date]:
    try:
        return date.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def parse_session(stored: StoredSession) -> SessionExecution:
    """One completed session's execution evidence, or the reason it has none.

    * the final acknowledged checkpoint is re-validated with the canonical
      write-path rules; anything it would refuse makes the session
      ``unavailable`` as a whole (no partial use of a corrupt snapshot);
    * execution context is read only through TI-01A's anchor-verified
      ``project_context``: unbound or stale context is absent, not guessed;
    * ``completed_at`` on a different Istanbul day than ``workout_date``
      excludes the session rather than re-dating it.
    """
    day = _parse_day(stored.workout_date)
    common = dict(
        ref=stored.ref, workout_date=day, completed_at=stored.completed_at,
        weekday_slot=stored.weekday_slot,
        checkpoint_revision=stored.checkpoint_revision,
    )
    if day is None or stored.completed_at is None or app_date_of(stored.completed_at) != day:
        return SessionExecution(availability=AVAILABILITY_INCONSISTENT, **common)
    try:
        exercises = parse_stored_exercises(load_snapshot(stored.checkpoint_data))
    except InvalidSessionRequest:
        return SessionExecution(availability=AVAILABILITY_UNAVAILABLE, **common)

    context = {(entry["exercise_id"], entry["index"]): entry
               for entry in project_context(stored)["sets"]}
    prescription = project_prescription(stored.prescription_data)
    targets = ({entry["exercise_id"]: entry for entry in prescription["exercises"]}
               if prescription else None)
    parsed = []
    for exercise in exercises:
        observations = []
        for item in exercise["sets"]:
            extra = context.get((exercise["exercise_id"], item["index"]))
            interval = extra["actual_rest"] if extra else None
            observations.append(SetObservation(
                index=item["index"], completed=item["completed"],
                reps=item["reps"], weight_kg=item["weight_kg"],
                actual_rir=extra["actual_rir"] if extra else None,
                tempo_adherence=extra["tempo_adherence"] if extra else None,
                interval_seconds=interval["seconds"] if interval else None,
                interval_method=interval["method"] if interval else None,
            ))
        parsed.append(ExerciseExecution(exercise["exercise_id"], tuple(observations)))
    return SessionExecution(
        availability=AVAILABILITY_OBSERVED, exercises=tuple(parsed),
        plan_lineage=prescription["source_plan_lineage"] if prescription else None,
        plan_version=prescription["source_mutation_version"] if prescription else None,
        prescription=targets, **common,
    )


# ── Calendar windows (server-side Istanbul days only) ───────────────────────
def week_start(day: date) -> date:
    """Monday of the Istanbul calendar week containing ``day``."""
    return day - timedelta(days=day.weekday())


def exposure_windows(anchor: date) -> tuple:
    """The two complete Monday–Sunday weeks before ``anchor``'s week.

    Returned oldest first: ``(prior, recent)``. Anchored on the session's own
    ``workout_date``, so the windows never depend on the read time.
    """
    monday = week_start(anchor)
    windows = []
    for weeks_back in range(EXPOSURE_WEEKS, 0, -1):
        start = monday - timedelta(days=7 * weeks_back)
        windows.append((start, start + timedelta(days=6)))
    return tuple(windows)


def _in_window(session: SessionExecution, window) -> bool:
    return session.workout_date is not None and window[0] <= session.workout_date <= window[1]


def coverage(sessions: Iterable[SessionExecution], window) -> Coverage:
    inside = [s for s in sessions if _in_window(s, window)]
    return Coverage(eligible_sessions=len(inside),
                    observed_sessions=sum(1 for s in inside if s.observed))


# ── Exposure and frequency ──────────────────────────────────────────────────
def weekly_set_exposure(sessions, exercise_id: str, window) -> ExposureFact:
    """Completed canonical set identities of one catalog exercise in a window.

    A set identity is (session, exercise, index); two sessions on one Istanbul
    day contribute their sets independently. No muscle-equivalent, effective or
    hard-set weighting. Unavailable sessions are counted in coverage, never as
    zero sets.
    """
    sessions = tuple(sessions)
    total = 0
    for session in sessions:
        if session.observed and _in_window(session, window):
            exercise = session.exercise(exercise_id)
            if exercise is not None:
                total += len(exercise.completed)
    return ExposureFact(exercise_id, tuple(window), total, coverage(sessions, window))


def exercise_frequency(sessions, exercise_id: str, window) -> FrequencyFact:
    """Distinct workout dates with at least one completed set of the exercise."""
    sessions = tuple(sessions)
    days = {
        session.workout_date for session in sessions
        if session.observed and _in_window(session, window)
        and session.exercise(exercise_id) is not None
        and session.exercise(exercise_id).completed
    }
    return FrequencyFact(tuple(window), len(days), coverage(sessions, window))


def training_frequency(sessions, window) -> FrequencyFact:
    """Distinct completed training dates.

    A canonical completion is itself the evidence of a training date, so a
    date-consistent completed session counts even when its checkpoint is
    unavailable; date-inconsistent sessions are excluded, never re-dated. This
    says nothing about how many sets were logged.
    """
    sessions = tuple(sessions)
    days = {
        session.workout_date for session in sessions
        if session.availability != AVAILABILITY_INCONSISTENT and _in_window(session, window)
    }
    return FrequencyFact(tuple(window), len(days), coverage(sessions, window))


# ── Volume ──────────────────────────────────────────────────────────────────
def rep_volume(sets) -> VolumeFact:
    """Sum of reps over completed sets. Null reps are excluded, zero is zero."""
    completed = [item for item in sets if item.completed]
    observed = [item.reps for item in completed if item.reps is not None]
    return VolumeFact(sum(observed) if observed else None, len(observed), len(completed))


def load_volume(sets) -> VolumeFact:
    """Sum of reps x weight_kg (unit kg·reps) over completed sets with BOTH values.

    Computed in exact tenths of a kilogram (the stored load precision), so the
    total does not depend on float summation order. No bodyweight, machine,
    cable or assistance inference; not a physiological workload.
    """
    completed = [item for item in sets if item.completed]
    paired = [item for item in completed if item.reps is not None and item.weight_kg is not None]
    if not paired:
        return VolumeFact(None, 0, len(completed))
    tenths = sum(item.reps * round(item.weight_kg * 10) for item in paired)
    return VolumeFact(tenths / 10, len(paired), len(completed))


# ── Recent performance ──────────────────────────────────────────────────────
def in_horizon(session: SessionExecution, current: SessionExecution) -> bool:
    """Ordered strictly before ``current`` and within the bounded day horizon."""
    if session.ref == current.ref or session.order_key >= current.order_key:
        return False
    if session.workout_date is None:
        return True
    return current.workout_date - timedelta(days=HISTORY_DAYS) <= session.workout_date


def recent_performance(history, exercise_id: str, current: SessionExecution) -> RecentPerformance:
    """The last <= 8 observed sessions before ``current`` that completed at
    least one set of the exercise, newest first by (completed_at, reference)."""
    window = [session for session in history if in_horizon(session, current)]
    found = sorted(
        (Occurrence(session, session.exercise(exercise_id)) for session in window
         if session.observed and session.exercise(exercise_id) is not None
         and session.exercise(exercise_id).completed),
        key=lambda occurrence: occurrence.session.order_key, reverse=True,
    )
    excluded = sum(1 for session in window if not session.observed)
    return RecentPerformance(exercise_id, tuple(found[:MAX_RECENT_SESSIONS]), excluded)


# ── Optional execution context ──────────────────────────────────────────────
def rir_fact(sets) -> RirFact:
    """Recorded actual RIR over completed sets. Unknown stays unknown."""
    completed = [item for item in sets if item.completed]
    recorded = [item.actual_rir for item in completed if item.actual_rir is not None]
    distribution = tuple((bucket, recorded.count(bucket)) for bucket in RIR_ORDER
                         if recorded.count(bucket))
    return RirFact(len(recorded), len(completed), distribution)


def lower_median(values) -> Optional[int]:
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2] if ordered else None


def logging_interval_fact(sets, method: str = LOGGING_INTERVAL_METHOD) -> IntervalFact:
    """Between-set logging intervals of ONE measurement method.

    Eligible: completed sets whose immediately preceding index is completed.
    Observations of any other method are never combined with this one. The
    result describes logging taps; it is not physiological rest.
    """
    by_index = {item.index: item for item in sets}
    eligible = [item for item in sets if item.completed and item.index >= 1
                and by_index.get(item.index - 1) is not None
                and by_index[item.index - 1].completed]
    samples = [item.interval_seconds for item in eligible
               if item.interval_seconds is not None and item.interval_method == method]
    return IntervalFact(method, len(samples), len(eligible), lower_median(samples))
