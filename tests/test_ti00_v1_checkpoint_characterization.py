"""TI-00: pin the shipped V1 wire semantics before designing negotiated V2.

These tests characterize current code only; future context is deliberately
rejected. Existing transport/PG suites own orchestration and race coverage.
"""
from copy import deepcopy

import pytest

from app.services.workout_session.checkpoint import (
    MAX_REVISION,
    parse_checkpoint,
    parse_revision,
    parse_revision_value,
)
from app.services.workout_session.errors import InvalidRevision, InvalidSessionRequest


EXERCISE = "ex_barbell_back_squat"
SNAPSHOT = {
    "current_exercise_index": 0,
    "elapsed_seconds": 120,
    "exercises": [{"exercise_id": EXERCISE, "sets": [
        {"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0},
    ]}],
}
CANONICAL_JSON = (
    '{"current_exercise_index":0,"elapsed_seconds":120,"exercises":'
    '[{"exercise_id":"ex_barbell_back_squat","sets":'
    '[{"completed":true,"index":0,"reps":8,"weight_kg":60.0}]}]}'
)
V1_FINGERPRINT = "72bb7841f30cb6abc8076f81d712a4526ffe8fd6090285ced2e09c00341502c1"


def test_v1_canonical_bytes_and_domain_digest_are_frozen():
    parsed = parse_checkpoint(deepcopy(SNAPSHOT), (EXERCISE,))
    assert parsed.to_json() == CANONICAL_JSON
    assert parsed.fingerprint == V1_FINGERPRINT
    equivalent = deepcopy(SNAPSHOT)
    equivalent["exercises"][0]["sets"][0]["weight_kg"] = 60
    assert parse_checkpoint(equivalent, (EXERCISE,)).fingerprint == V1_FINGERPRINT


@pytest.mark.parametrize("field,value", [
    ("actual_rir", "0"),
    ("target_rir", "2"),
    ("tempo_adherence", "lost_control"),
    ("actual_rest", {"seconds":90,"method":"completion_gap",
                     "quality":"foreground_contiguous"}),
    ("note", "Seat position 4"),
])
def test_v1_cannot_silently_accept_future_set_context(field, value):
    richer = deepcopy(SNAPSHOT)
    richer["exercises"][0]["sets"][0][field] = value
    with pytest.raises(InvalidSessionRequest):
        parse_checkpoint(richer, (EXERCISE,))


def test_v1_missing_measurements_do_not_fingerprint_as_zero():
    missing = deepcopy(SNAPSHOT)
    zero = deepcopy(SNAPSHOT)
    missing_set = missing["exercises"][0]["sets"][0]
    zero_set = zero["exercises"][0]["sets"][0]
    missing_set.update(reps=None, weight_kg=None)
    zero_set.update(reps=0, weight_kg=0)
    absent = parse_checkpoint(missing, (EXERCISE,))
    recorded = parse_checkpoint(zero, (EXERCISE,))
    assert absent.snapshot["exercises"][0]["sets"][0]["weight_kg"] is None
    assert recorded.snapshot["exercises"][0]["sets"][0]["weight_kg"] == 0.0
    assert absent.fingerprint != recorded.fingerprint


def test_v1_header_and_body_revision_ceiling_agree():
    assert MAX_REVISION == 999_999_999
    assert parse_revision(' "999999999" ') == MAX_REVISION
    assert parse_revision_value(MAX_REVISION) == MAX_REVISION
    with pytest.raises(InvalidRevision):
        parse_revision(str(MAX_REVISION + 1))
    with pytest.raises(InvalidRevision):
        parse_revision_value(MAX_REVISION + 1)
    with pytest.raises(InvalidRevision):
        parse_revision_value(True)
