"""Closed v1 wire DTOs over the checked TD-01 projection; no selection or I/O."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import TypedDict

from app.services.exercise_catalog import ID_PATTERN
from app.services.exercise_performance_history.models import (
    HISTORY_DAYS, MAX_OCCURRENCES, MAX_SCAN_ROWS, ExerciseHistory, ProjectionInvariantError,
)
from app.services.exercise_performance_history.selection import check_invariants
from app.services.workout_session.checkpoint import MAX_REVISION, MAX_SETS_PER_EXERCISE

CONTRACT_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024


class SetDTO(TypedDict):
    index: int
    reps: int | None
    weight_kg: float | None


class ReferenceDTO(TypedDict):
    session_ref: str
    checkpoint_revision: int


class OccurrenceDTO(ReferenceDTO):
    workout_date: str
    completed_at: str
    sets: list[SetDTO]


class UnavailableDTO(TypedDict):
    workout_date: str | None
    completed_at: str | None
    reason: str


class WindowDTO(TypedDict):
    start_date: str
    end_date: str
    timezone: str


class CoverageDTO(TypedDict):
    complete: bool
    scanned_sessions: int
    excluded_cross_date: int
    excluded_missing_completed_at: int
    unavailable_sessions: list[UnavailableDTO]
    scan_limit_reached: bool
    occurrence_limit_reached: bool


class HistoryDTO(TypedDict):
    exercise_id: str
    state: str
    window: WindowDTO
    latest_valid_occurrence_ref: ReferenceDTO | None
    occurrences: list[OccurrenceDTO]
    coverage: CoverageDTO


class HistoryEnvelopeDTO(TypedDict):
    contract_version: int
    exercise_performance_history: HistoryDTO


def _require(condition: bool) -> None:
    if not condition:
        raise ProjectionInvariantError('history wire invariant failed')


def _day(value: date) -> str:
    _require(type(value) is date)
    return value.isoformat()


def _timestamp(value: datetime) -> str:
    _require(type(value) is datetime and value.tzinfo is None)
    return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def history_payload(history: ExerciseHistory) -> HistoryEnvelopeDTO:
    """Copy canonical facts in server order; refuse malformed wire identities.

    PR1 owns eligibility/state/order. Checks here add only wire types, identity
    lengths, canonical revision and collection bounds, and coverage counts.
    """
    _require(isinstance(history, ExerciseHistory))
    check_invariants(history)
    _require(type(history.exercise_id) is str and 1 <= len(history.exercise_id) <= 64
             and ID_PATTERN.fullmatch(history.exercise_id) is not None)
    _day(history.window_start)
    _day(history.window_end)
    _require((history.window_end - history.window_start).days == HISTORY_DAYS)
    coverage = history.coverage
    for count in (coverage.scanned_sessions, coverage.excluded_cross_date,
                  coverage.excluded_missing_completed_at):
        _require(type(count) is int and 0 <= count <= MAX_SCAN_ROWS)
    _require(type(coverage.scan_limit_reached) is bool
             and type(coverage.occurrence_limit_reached) is bool)
    _require(coverage.scan_limit_reached == (coverage.scanned_sessions == MAX_SCAN_ROWS))
    _require(isinstance(coverage.unavailable, tuple) and len(coverage.unavailable) <= MAX_SCAN_ROWS)
    _require(isinstance(history.occurrences, tuple))
    _require(len(history.occurrences) + len(coverage.unavailable)
             + coverage.excluded_cross_date + coverage.excluded_missing_completed_at
             <= coverage.scanned_sessions)
    _require(not coverage.occurrence_limit_reached or len(history.occurrences) == MAX_OCCURRENCES)
    occurrences: list[OccurrenceDTO] = []
    for item in history.occurrences:
        _require(type(item.session_ref) is str and 1 <= len(item.session_ref) <= 64)
        _require(type(item.checkpoint_revision) is int and 0 <= item.checkpoint_revision <= MAX_REVISION)
        _require(len(item.sets) <= MAX_SETS_PER_EXERCISE)
        _day(item.workout_date)
        _require(history.window_start <= item.workout_date <= history.window_end)
        occurrences.append(OccurrenceDTO(
            session_ref=item.session_ref, checkpoint_revision=item.checkpoint_revision,
            workout_date=_day(item.workout_date), completed_at=_timestamp(item.completed_at),
            sets=[SetDTO(index=fact.index, reps=fact.reps, weight_kg=fact.weight_kg)
                  for fact in item.sets]))
    unavailable: list[UnavailableDTO] = []
    for marker in coverage.unavailable:
        if marker.workout_date is not None:
            _day(marker.workout_date)
            _require(history.window_start <= marker.workout_date <= history.window_end)
        unavailable.append(UnavailableDTO(
            workout_date=None if marker.workout_date is None else _day(marker.workout_date),
            completed_at=None if marker.completed_at is None else _timestamp(marker.completed_at),
            reason=marker.reason))
    latest = (ReferenceDTO(session_ref=occurrences[0]['session_ref'],
                           checkpoint_revision=occurrences[0]['checkpoint_revision'])
              if occurrences else None)
    return HistoryEnvelopeDTO(contract_version=CONTRACT_VERSION, exercise_performance_history=HistoryDTO(
        exercise_id=history.exercise_id, state=history.state,
        window=WindowDTO(start_date=_day(history.window_start), end_date=_day(history.window_end),
                         timezone='Europe/Istanbul'),
        latest_valid_occurrence_ref=latest, occurrences=occurrences,
        coverage=CoverageDTO(complete=coverage.complete, scanned_sessions=coverage.scanned_sessions,
                             excluded_cross_date=coverage.excluded_cross_date,
                             excluded_missing_completed_at=coverage.excluded_missing_completed_at,
                             unavailable_sessions=unavailable, scan_limit_reached=coverage.scan_limit_reached,
                             occurrence_limit_reached=coverage.occurrence_limit_reached)))


def history_json(history: ExerciseHistory) -> bytes:
    """Measure and send the same compact UTF-8 bytes; never truncate facts/gaps."""
    encoded = json.dumps(history_payload(history), ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    _require(len(encoded) <= MAX_PAYLOAD_BYTES)
    return encoded
