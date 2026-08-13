"""Legacy Pump Check contract frozen before the Sprint 10 additive adapter."""
from datetime import datetime

import s3_helper

from app.extensions import db
from app.models import Friendship, PumpCheck
from app.services.pump_checks import can_view_pump_check
from app.services.workout_completion import (
    CompleteWorkoutCommand,
    CompletionOutcome,
    complete_workout,
)
from app.timeutil import app_today


def _legacy_command(user_id):
    return CompleteWorkoutCommand(
        user_id=user_id,
        today=app_today(),
        image_key=f"pump-checks/{user_id}/2026/08/photo.jpg",
        location_type="gym",
        description="Upper-body session",
        workout_score=8.5,
        visibility="private",
        valid=True,
        fallback=False,
        activity_text="completed",
        entry_path="characterization",
    )


def test_legacy_completion_persists_authoritative_pump_check_fields(app, make_user):
    user = make_user("legacy-create")

    result = complete_workout(_legacy_command(user.id))
    row = db.session.get(PumpCheck, result.pump_check_id)

    assert result.outcome is CompletionOutcome.CREATED
    assert row.user_id == user.id
    assert row.image_key == f"pump-checks/{user.id}/2026/08/photo.jpg"
    assert row.location_type == "gym"
    assert row.description == "Upper-body session"
    assert row.workout_score == 8.5
    assert row.visibility == "private"
    assert row.valid is True
    assert row.fallback is False
    assert row.date_key == app_today().isoformat()
    assert isinstance(row.created_at, datetime)


def test_legacy_daily_uniqueness_replays_without_second_row(app, make_user):
    user = make_user("legacy-replay")

    first = complete_workout(_legacy_command(user.id))
    second = complete_workout(_legacy_command(user.id))

    assert first.outcome is CompletionOutcome.CREATED
    assert second.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1


def test_legacy_gallery_is_owner_scoped_and_signs_only_owned_key(
        client, auth_user, make_user, monkeypatch):
    other = make_user("legacy-other")
    mine = PumpCheck(
        user_id=auth_user.id,
        image_key=f"pump-checks/{auth_user.id}/2026/08/mine.jpg",
        location_type="home",
        description="Mine",
        valid=True,
    )
    theirs = PumpCheck(
        user_id=other.id,
        image_key=f"pump-checks/{other.id}/2026/08/theirs.jpg",
        description="Theirs",
        valid=True,
    )
    db.session.add_all([mine, theirs])
    db.session.commit()
    calls = []
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)

    def sign(key, expires_in=3600, expected_user_id=None):
        calls.append((key, expires_in, expected_user_id))
        return "https://media.example.test/owned"

    monkeypatch.setattr(s3_helper, "generate_presigned_url", sign)

    response = client.get("/pump-check-gallery/data")

    assert response.status_code == 200
    assert [item["description"] for item in response.get_json()["items"]] == ["Mine"]
    assert calls == [(mine.image_key, 3600, auth_user.id)]


def test_legacy_private_visibility_and_delete_do_not_cross_owner(
        client, auth_user, make_user):
    other = make_user("legacy-delete-other")
    db.session.add(Friendship(
        sender_id=auth_user.id, receiver_id=other.id, status="accepted"))
    row = PumpCheck(user_id=other.id, visibility="private", valid=True)
    db.session.add(row)
    db.session.commit()

    assert can_view_pump_check(auth_user.id, row) is False
    response = client.delete(f"/pump-check-gallery/{row.id}")

    assert response.status_code == 404
    assert db.session.get(PumpCheck, row.id) is not None
