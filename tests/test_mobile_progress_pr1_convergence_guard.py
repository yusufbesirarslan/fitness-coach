"""Native Progress PR1 — executable evidence for the discovery specification.

See ``docs/mobile/progress-vertical-slice.md``. These tests pin two facts the
specification depends on:

1. (regression, defect D1 — fixed in Native Progress PR2) A standalone
   ``POST /api/v1/pump-checks`` made earlier on the same Istanbul day must not
   make the canonical WorkoutSession completion answer ``already_completed``.
   Before PR2, ``already_completed_today`` counted *any* PumpCheck by
   ``created_at`` and the completion wrote no marker, XP or proof. Completion
   evidence is now the canonical ``date_key`` claim only; the full matrix lives in
   ``tests/test_progress_pump_check_completion_proof.py``.

2. (characterization) The completion-proof PumpCheck written by the canonical
   completion transaction has no ``public_id`` and no ``captured_at``, so it is
   absent from the owner-private native history and cannot be addressed by the
   native detail route. Native Progress must not read completion from Pump Check
   history.
"""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import WORKOUT_COMPLETION_MARKER, PumpCheck, WorkoutLog
from app.services.mobile_pump_checks import service as pump_check_service
from tests.test_mobile_pump_check_api import _analysis, _image
from tests.test_mobile_workout_sessions_api import (  # noqa: F401 - fixtures
    _complete,
    _start,
    as_mobile,
    completion_proof,
    owner,
    plan,
    sessions_enabled,
    workout_ref,
)

PUMP_CHECKS_PATH = "/api/v1/pump-checks"
# 09:00 UTC is 12:00 in Istanbul on the session suite's fixed day (2026-07-23).
FIXED_DAY_UTC_MORNING = datetime(2026, 7, 23, 9, 0)


@pytest.fixture
def standalone_dependencies(monkeypatch):
    monkeypatch.setattr(pump_check_service.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(
        pump_check_service.s3_helper, "upload_image",
        lambda *args, **kwargs: "pump-checks/private.jpg")
    monkeypatch.setattr(
        pump_check_service.s3_helper, "generate_presigned_url",
        lambda *args, **kwargs: "https://media.example.test/temporary")
    monkeypatch.setattr(
        pump_check_service, "analyze_image", lambda *args, **kwargs: _analysis())


def _create_standalone_on_fixed_day(client, headers):
    response = client.post(
        PUMP_CHECKS_PATH,
        headers={**headers, "Idempotency-Key": "standalone-progress-0001"},
        data={
            "image": (_image(), "pump.jpg", "image/jpeg"),
            "body_region": "upper_body",
            "environment": "gym",
            "description": "",
            "captured_at": datetime.utcnow().replace(microsecond=0).isoformat()
            + "Z",
        },
    )
    assert response.status_code == 201
    row = PumpCheck.query.one()
    assert row.date_key is None
    # created_at is stamped by the column default from the real clock; move the
    # row into the suite's fixed Istanbul day, which is what production sees when
    # both requests happen on the same calendar day.
    row.created_at = FIXED_DAY_UTC_MORNING
    db.session.commit()


def test_standalone_pump_check_does_not_suppress_canonical_completion(
    client, owner, as_mobile, workout_ref, completion_proof,
    standalone_dependencies,
):
    headers = as_mobile(owner)
    _create_standalone_on_fixed_day(client, headers)
    reference = _start(client, headers, workout_ref).json["session"][
        "session_ref"]

    done = _complete(client, headers, reference, 0)

    assert done.status_code == 200
    assert done.json["completion"]["outcome"] == "created"
    assert WorkoutLog.query.filter_by(
        user_id=owner.id, exercise_name=WORKOUT_COMPLETION_MARKER).count() == 1
    assert completion_proof["validate"] == 1


def test_completion_proof_is_not_part_of_native_pump_check_history(
    client, owner, as_mobile, workout_ref, completion_proof,
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"][
        "session_ref"]
    assert _complete(client, headers, reference, 0).json["completion"][
        "outcome"] == "created"

    proof = PumpCheck.query.filter_by(user_id=owner.id).one()
    assert proof.date_key is not None
    assert proof.public_id is None
    assert proof.captured_at is None

    history = client.get(PUMP_CHECKS_PATH, headers=headers)
    assert history.status_code == 200
    assert history.json["pump_checks"] == []
    assert history.json["has_more"] is False
