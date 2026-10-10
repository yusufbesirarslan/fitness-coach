"""H-X1: TD-01 and TI-03 agree on the canonical fact semantics they share.

One row matrix is fed to TI-03 ``facts.parse_session`` and to TD-01
``selection.classify``. Only ADR 0002 fact semantics are compared: checkpoint
acceptance/rejection, performed-set eligibility, core set values, dating and
the canonical exception boundary. TI-03's response states, coverage lumping,
ordering, recommendation behaviour and RIR/tempo fields are deliberately NOT
compared. Tests may import both packages; production code imports neither from
the other.
"""
import json
from dataclasses import replace
from datetime import datetime

import pytest

from app.services.exercise_performance_history import selection
from app.services.exercise_performance_history.models import (
    REASON_CHECKPOINT_INVALID,
    REASON_CHECKPOINT_MISSING,
    ROW_ELIGIBLE,
    ROW_EXCLUDED,
    ROW_UNAVAILABLE,
    CandidateRow,
)
from app.services.training_intelligence import facts
from app.services.training_intelligence.models import (
    AVAILABILITY_INCONSISTENT,
    AVAILABILITY_OBSERVED,
    AVAILABILITY_UNAVAILABLE,
)
from tests.ti03_support import BENCH, SQUAT, context_entry, day, stored, straight

_VALID = [(SQUAT, [(0, True, 8, 60.0), (1, False, 9, 62.5), (2, True, None, 0.0), (3, True, 0, None)]),
          (BENCH, straight(40.0, 10, 9))]


def _corrupt(raw):
    return replace(stored("c", day(-2), [(SQUAT, straight(60.0, 8))]), checkpoint_data=raw)


MATRIX = {
    "valid": stored("v", day(-1), _VALID, context=[context_entry(SQUAT, 0, rir="2")]),
    "valid_no_context": stored("v1", day(-3), _VALID, v1=True),
    "valid_revision_zero": stored("r0", day(-4), _VALID, revision=0),
    "valid_empty_exercises": stored("e", day(-4), [], v1=True, prescription_data=""),
    "missing_rev0": replace(stored("m", day(-5), _VALID), checkpoint_data=None, checkpoint_revision=0),
    "null_rev3": replace(stored("n", day(-5), _VALID), checkpoint_data=None),
    "empty_string": _corrupt(""),
    "blank": _corrupt(" "),
    "not_json": _corrupt("not json"),
    "array": _corrupt("[1, 2]"),
    "wrong_keys": _corrupt(json.dumps({"exercises": []})),
    "duplicate_exercise": _corrupt(json.dumps({"current_exercise_index": 0, "elapsed_seconds": 1,
                                              "exercises": [{"exercise_id": SQUAT, "sets": []}] * 2})),
    "duplicate_set": _corrupt(json.dumps({"current_exercise_index": 0, "elapsed_seconds": 1,
                                         "exercises": [{"exercise_id": SQUAT, "sets": [
                                             {"index": 0, "completed": True, "reps": 1, "weight_kg": 1}] * 2}]})),
    "bad_reps": _corrupt(json.dumps({"current_exercise_index": 0, "elapsed_seconds": 1,
                                    "exercises": [{"exercise_id": SQUAT, "sets": [
                                        {"index": 0, "completed": True, "reps": -1, "weight_kg": 1}]}]})),
    "nan_weight": _corrupt('{"current_exercise_index": 0, "elapsed_seconds": 1, "exercises": '
                           '[{"exercise_id": "ex_barbell_back_squat", "sets": [{"index": 0, '
                           '"completed": true, "reps": 1, "weight_kg": NaN}]}]}'),
    "int_completed": _corrupt(json.dumps({"current_exercise_index": 0, "elapsed_seconds": 1,
                                         "exercises": [{"exercise_id": SQUAT, "sets": [
                                             {"index": 0, "completed": 1, "reps": 1, "weight_kg": 1}]}]})),
    "null_completed_at": replace(stored("t", day(-6), _VALID), completed_at=None),
    "cross_date": stored("x", day(-6), _VALID, completed_at=datetime(2026, 9, 25, 21, 10)),
    "cross_date_and_corrupt": replace(stored("xc", day(-6), _VALID,
                                             completed_at=datetime(2026, 9, 25, 21, 10)),
                                      checkpoint_data="{x"),
    "null_completed_at_and_missing": replace(stored("tm", day(-6), _VALID), completed_at=None,
                                             checkpoint_data=None, checkpoint_revision=0),
}


def _candidate(row):
    return CandidateRow(public_id=row.ref, workout_date=row.workout_date,
                        completed_at=row.completed_at, checkpoint_revision=row.checkpoint_revision,
                        checkpoint_data=row.checkpoint_data)


_EQUIVALENT = {
    AVAILABILITY_OBSERVED: {(ROW_ELIGIBLE, None)},
    AVAILABILITY_UNAVAILABLE: {(ROW_UNAVAILABLE, REASON_CHECKPOINT_MISSING),
                               (ROW_UNAVAILABLE, REASON_CHECKPOINT_INVALID)},
    AVAILABILITY_INCONSISTENT: {(ROW_EXCLUDED, "cross_date"), (ROW_EXCLUDED, "missing_completed_at")},
}


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_h_x1_row_classes_agree(name):
    row = MATRIX[name]
    ti = facts.parse_session(row)
    td = selection.classify(_candidate(row))
    assert (td.row_class, td.reason) in _EQUIVALENT[ti.availability], (name, ti.availability, td)


@pytest.mark.parametrize("name", sorted(name for name in MATRIX
                                        if facts.parse_session(MATRIX[name]).availability
                                        == AVAILABILITY_OBSERVED))
def test_h_x1_performed_sets_and_values_agree(name):
    row = MATRIX[name]
    ti = facts.parse_session(row)
    td = selection.classify(_candidate(row))
    ti_facts = {exercise.exercise_id: tuple((item.index, item.reps, item.weight_kg)
                                            for item in exercise.sets if item.completed is True)
                for exercise in ti.exercises}
    td_facts = {entry["exercise_id"]: tuple((item.index, item.reps, item.weight_kg)
                                            for item in selection.performed_sets(td.exercises,
                                                                                 entry["exercise_id"]))
                for entry in td.exercises}
    assert td_facts == ti_facts
    for values in td_facts.values():
        for _, reps, weight in values:
            assert reps is None or type(reps) is int
            assert weight is None or type(weight) is float


def test_h_x1_matrix_covers_every_shared_class():
    seen = {facts.parse_session(row).availability for row in MATRIX.values()}
    assert seen == {AVAILABILITY_OBSERVED, AVAILABILITY_UNAVAILABLE, AVAILABILITY_INCONSISTENT}


@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyError("bug"), ValueError("bug")],
                         ids=["RuntimeError", "KeyError", "ValueError"])
def test_h_x1_unexpected_exceptions_propagate_from_both(monkeypatch, error):
    def exploding(_snapshot):
        raise error
    monkeypatch.setattr(facts, "parse_stored_exercises", exploding)
    monkeypatch.setattr(selection, "parse_stored_exercises", exploding)
    row = MATRIX["valid"]
    with pytest.raises(type(error)) as ti_caught:
        facts.parse_session(row)
    with pytest.raises(type(error)) as td_caught:
        selection.classify(_candidate(row))
    assert ti_caught.value is error and td_caught.value is error


def test_h_x1_pathological_nesting_propagates_from_both():
    row = _corrupt("[" * 200_000 + "]" * 200_000)
    with pytest.raises(RecursionError):
        facts.parse_session(row)
    with pytest.raises(RecursionError):
        selection.classify(_candidate(row))
