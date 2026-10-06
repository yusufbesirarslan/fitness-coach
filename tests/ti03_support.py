"""Deterministic TI-03 fixture builders shared by the unit, API and PG suites.

Every stored column is produced by the canonical TI-01A writers
(``parse_v2`` + ``bind_context`` for execution context, the exact prescription
envelope for start snapshots), so fixtures can only express states the real
write path can persist -- plus the explicitly corrupt variants tests ask for.
"""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from app.services.training_intelligence.facts import parse_session
from app.services.training_intelligence.models import StoredSession
from app.services.workout_session.checkpoint import _canonical_json
from app.services.workout_session.context import bind_context, parse_v2

SQUAT = "ex_barbell_back_squat"
BENCH = "ex_barbell_bench_press"
ROW = "ex_barbell_row"
DEADLIFT = "ex_barbell_deadlift"
PUSH_UP = "ex_push_up"
RUN = "ex_outdoor_run"
LINEAGE = "ti03-lineage-a"
VERSION = 3
THURSDAY = "Perşembe"
MONDAY = "Pazartesi"
# Thursday 2026-10-01; its Monday is 2026-09-28. Complete prior weeks:
# recent = 2026-09-21..27, prior = 2026-09-14..20.
ANCHOR = date(2026, 10, 1)
_TARGETS = ("target_reps", "target_load_kg", "target_rir", "target_tempo", "planned_rest_seconds")


def day(offset_days):
    """ANCHOR shifted by whole days."""
    return ANCHOR + timedelta(days=offset_days)


def utc_noon(on, minute=0):
    """A naive-UTC completed_at whose Istanbul date is ``on`` (15:00 local)."""
    return datetime(on.year, on.month, on.day, 12, minute)


def prescription(exercises, *, lineage=LINEAGE, version=VERSION, targets=None):
    """Exact TI-01A start snapshot. ``exercises`` maps id -> prescribed sets."""
    targets = targets or {}
    entries = []
    for exercise_id, sets in exercises.items():
        values = {"target_reps": {"min": 8, "max": 10}, "target_load_kg": None,
                  "target_rir": None, "target_tempo": None, "planned_rest_seconds": 90}
        values.update(targets.get(exercise_id, {}))
        entries.append({"exercise_id": exercise_id, "sets": sets, **values,
                        "provenance": {key: ("structured" if key in targets.get(exercise_id, {})
                                             else "legacy_parsed" if values[key] is not None
                                             else "unavailable") for key in _TARGETS}})
    return _canonical_json({"schema_version": 1, "source_plan_lineage": lineage,
                            "source_mutation_version": version, "exercises": entries})


def checkpoint(exercises):
    """``exercises``: ordered ``[(exercise_id, [(index, completed, reps, kg), ...])]``."""
    return {"current_exercise_index": 0, "elapsed_seconds": 1200, "exercises": [
        {"exercise_id": exercise_id, "sets": [
            {"index": index, "completed": completed, "reps": reps, "weight_kg": weight}
            for index, completed, reps, weight in sets]}
        for exercise_id, sets in exercises]}


def context_entry(exercise_id, index, rir=None, tempo=None, gap=None):
    return {"exercise_id": exercise_id, "index": index, "actual_rir": rir,
            "tempo_adherence": tempo,
            "actual_rest": None if gap is None else {
                "seconds": gap, "method": "completion_gap", "quality": "foreground_contiguous"}}


def stored(ref, on, exercises, *, minute=0, slot=THURSDAY, context=None, revision=4,
           status="completed", prescribed=None, prescription_data=None, lineage=LINEAGE,
           version=VERSION, targets=None, completed_at=None, checkpoint_data=None,
           execution_context_data=None, v1=False):
    """One StoredSession built through the canonical writers.

    ``prescribed`` defaults to 3 sets for every exercise; pass
    ``prescription_data=""`` for a pre-TI-01A row with no snapshot. ``v1=True``
    stores no execution context at all.
    """
    snapshot = checkpoint(exercises)
    if prescription_data is None:
        prescription_data = prescription(
            prescribed or {exercise_id: 3 for exercise_id, _ in exercises},
            lineage=lineage, version=version, targets=targets)
    prescription_data = prescription_data or None
    if execution_context_data is None and not v1:
        allowed = tuple(exercise_id for exercise_id, _ in exercises)
        parsed = parse_v2({"checkpoint": snapshot, "execution_context": {
            "schema_version": 1, "sets": context or []}}, allowed)
        execution_context_data = bind_context(
            SimpleNamespace(prescription_data=prescription_data), parsed, revision)
    return StoredSession(
        ref=ref, status=status, workout_date=on.isoformat(),
        completed_at=completed_at if completed_at is not None else utc_noon(on, minute),
        weekday_slot=slot, checkpoint_revision=revision,
        checkpoint_data=checkpoint_data if checkpoint_data is not None else json.dumps(snapshot),
        execution_context_data=execution_context_data,
        prescription_data=prescription_data,
    )


def session(*args, **kwargs):
    return parse_session(stored(*args, **kwargs))


def straight(load, *reps, completed=True):
    """Straight sets at one load: ``[(0, True, reps0, load), ...]``."""
    return [(index, completed, count, load) for index, count in enumerate(reps)]
