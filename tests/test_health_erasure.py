"""A1 = O2-B: F01 withdrawal erases health records, keeps ordinary account data.

Real database, real owner-checked S3 helpers and the canonical models; only the
S3 client is faked (account_deletion_support). Every case seeds a fully
populated account and a second account that must stay untouched.

    python -m pytest tests/test_health_erasure.py -v
"""
from datetime import datetime, timedelta

import pytest

from account_deletion_support import install_fakes, rows_exist, seed_account
from app.extensions import db
from app.health_consent_models import HealthConsentEvent, HealthErasure, HealthLegalHold
from app.models import (
    CoachMessage, CognitoSession, FeedHide, FeedItem, MealLog, Notification,
    PumpCheck, TrainingPlanGenerationOperation, User, UserBadge, UserSession,
    UserWearableConnection, WeeklyCheckIn, WeeklyWinner, WorkoutLog,
)
from app.services import account_eligibility, health_consent as consent, health_erasure
from tests.test_health_consent import command

# Ordinary records: never F01-based, so withdrawal must leave them in place.
ORDINARY = (TrainingPlanGenerationOperation, UserBadge, WeeklyWinner,
            UserWearableConnection, CognitoSession, Notification, FeedHide, FeedItem)


@pytest.fixture
def world(app, make_user, monkeypatch):
    _, s3 = install_fakes(monkeypatch)
    alice = make_user("alice_o2b", age=17, gender="female")
    bob = make_user("bob_o2b")
    a_own, a_keys = seed_account(alice, s3, "A")
    b_own, b_keys = seed_account(bob, s3, "B")
    alice = db.session.get(User, alice.id)
    alice.weight, alice.height, alice.target_weight, alice.goal_type = 61.0, 170.0, 58.0, "cut"
    alice.streak_count, alice.weekly_xp = 4, 120
    alice.user_metadata = {"injuries": "left knee", "dietary_restrictions": "celiac",
                           account_eligibility.METADATA_KEY: {"signed": "record"},
                           "pending_referral_code": "REF1",
                           health_erasure.COACH_INDEX_KEY: {"1": "sig"}}
    db.session.commit()

    class World:
        pass
    w = World()
    w.s3, w.a_id, w.b_id = s3, alice.id, bob.id
    w.a_own, w.b_own, w.a_keys, w.b_keys = a_own, b_own, a_keys, b_keys
    return w


def _withdraw(owner, generation=1, request_id="o2b-withdraw-0001"):
    return consent.mutate(owner, command(generation, "withdraw", request_id))


def _kinds(own, kinds):
    return [(model, row_id) for model, row_id in own if model in kinds]


def _health(own):
    return [(model, row_id) for model, row_id in own if model not in ORDINARY]


def test_withdrawal_erases_health_and_keeps_ordinary_data(world):
    result = _withdraw(world.a_id)
    assert result["state"] == "withdrawn" and result["erasure"]["state"] == "pending"
    assert health_erasure.run(world.a_id) == "completed"

    # Every health row is gone, every ordinary row stays.
    assert rows_exist(_health(world.a_own)) == [
        (m.__name__, i) for m, i in _health(world.a_own)]
    assert rows_exist(_kinds(world.a_own, ORDINARY)) == []
    assert UserSession.query.filter_by(user_id=world.a_id).count() == 0
    operation = TrainingPlanGenerationOperation.query.filter_by(user_id=world.a_id).one()
    assert operation.candidate_plan_data is None and operation.idempotency_key == "gen-A"

    alice = db.session.get(User, world.a_id)
    assert (alice.weight, alice.height, alice.target_weight, alice.goal_type) == (None,) * 4
    from app.services import account_profile
    assert account_profile.onboarding_state(alice).complete is False
    assert alice.streak_count == 0 and alice.weekly_xp == 0
    assert alice.username == "alice_o2b" and alice.age == 17 and alice.gender == "female"
    assert alice.full_name == "Private Name A"
    assert alice.profile_picture_key == world.a_keys["avatar"]
    assert set(alice.user_metadata) == {account_eligibility.METADATA_KEY, "pending_referral_code"}

    # Pump Check images and meal photos are released; the avatar is not.
    released = {world.a_keys[k] for k in ("pump", "pump2", "meal", "meal_pending")}
    assert released <= set(world.s3.deleted)
    assert world.a_keys["avatar"] in world.s3.objects

    # Permission evidence stays; the erasure is recorded without health content.
    assert HealthConsentEvent.query.filter_by(owner_id=world.a_id).count() == 2
    row = HealthErasure.query.filter_by(owner_id=world.a_id).one()
    assert row.completed_at is not None and row.summary["held"] == []
    assert consent.status(world.a_id)["erasure"]["state"] == "completed"

    # The other account is untouched.
    assert rows_exist(world.b_own) == []
    assert not set(world.b_keys.values()) & set(world.s3.deleted)


def test_regrant_is_refused_while_erasure_is_pending_and_restores_nothing(world):
    world.s3.failing.add(world.a_keys["pump"])
    _withdraw(world.a_id)
    with pytest.raises(health_erasure.ErasureUnavailable):
        health_erasure.run(world.a_id)
    # Storage failed first, so no database row changed yet.
    assert PumpCheck.query.filter_by(user_id=world.a_id).count() == 2
    with pytest.raises(consent.HealthConsentDenied) as refused:
        consent.mutate(world.a_id, command(2, request_id="o2b-regrant-0001"))
    assert (refused.value.code, refused.value.status) == ("HEALTH_ERASURE_PENDING", 409)
    assert consent.status(world.a_id)["state"] == "withdrawn"

    world.s3.failing.clear()
    assert health_erasure.run_pending()["completed"] == 1
    granted = consent.mutate(world.a_id, command(2, request_id="o2b-regrant-0002"))
    assert granted["state"] == "granted"
    # A new grant starts from no health history.
    assert PumpCheck.query.filter_by(user_id=world.a_id).count() == 0
    assert MealLog.query.filter_by(user_id=world.a_id).count() == 0
    assert db.session.get(User, world.a_id).weight is None


def test_legal_hold_keeps_only_named_categories_until_released(app, world):
    review = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d")
    with app.app_context():
        added = app.test_cli_runner().invoke(args=[
            "health-legal-hold-add", "--user-id", str(world.a_id), "--matter", "CLAIM-2026-001",
            "--review-date", review, "--category", "coach", "--category", "checkins"])
    assert added.exit_code == 0, added.output
    _withdraw(world.a_id)
    assert health_erasure.run(world.a_id) == "held"
    assert CoachMessage.query.count() >= 1
    assert WeeklyCheckIn.query.filter_by(user_id=world.a_id).count() == 1
    assert PumpCheck.query.filter_by(user_id=world.a_id).count() == 0
    assert WorkoutLog.query.filter_by(user_id=world.a_id).count() == 0
    assert db.session.get(User, world.a_id).weight is None
    # Held records stay unreachable: no new grant while the erasure is open.
    assert consent.status(world.a_id)["erasure"]["state"] == "pending"
    with pytest.raises(consent.HealthConsentDenied):
        consent.mutate(world.a_id, command(2, request_id="o2b-held-regrant"))

    hold = HealthLegalHold.query.filter_by(owner_id=world.a_id).one()
    released = app.test_cli_runner().invoke(args=[
        "health-legal-hold-release", "--hold-id", str(hold.id)])
    assert released.exit_code == 0 and '"completed"' in released.output
    assert WeeklyCheckIn.query.filter_by(user_id=world.a_id).count() == 0
    assert consent.status(world.a_id)["erasure"]["state"] == "completed"


@pytest.mark.parametrize("args", [
    ["--category", "everything"],
    ["--category", "coach", "--review-date", "2000-01-01"],
    ["--category", "coach", "--matter", " "],
])
def test_legal_hold_requires_named_categories_matter_and_future_review(app, world, args):
    base = {"--user-id": str(world.a_id), "--matter": "CLAIM-1",
            "--review-date": (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")}
    for flag in ("--matter", "--review-date"):
        if flag in args:
            base.pop(flag)
    flat = [x for kv in base.items() for x in kv] + args
    result = app.test_cli_runner().invoke(args=["health-legal-hold-add", *flat])
    assert result.exit_code != 0
    assert HealthLegalHold.query.count() == 0


def test_decline_erases_only_after_a_grant(make_user, world):
    legacy = make_user("legacy_o2b", health_consent=False, weight=90.0)
    consent.mutate(legacy.id, command(0, "decline", "o2b-legacy-decline"))
    assert consent.status(legacy.id)["erasure"] is None
    assert db.session.get(User, legacy.id).weight == 90.0

    declined = consent.mutate(world.a_id, command(1, "decline", "o2b-decline-after-grant"))
    assert declined["erasure"]["state"] == "pending"
    assert health_erasure.run(world.a_id) == "completed"
    assert PumpCheck.query.filter_by(user_id=world.a_id).count() == 0


def test_deleted_account_closes_its_pending_erasure(world):
    from app.cli import _purge_user
    _withdraw(world.a_id)
    _purge_user(db.session.get(User, world.a_id))
    db.session.commit()
    assert health_erasure.run(world.a_id) == "completed"
    row = HealthErasure.query.filter_by(owner_id=world.a_id).one()
    assert row.summary == {"account_deleted": True}


def test_account_deletion_purges_legal_holds(world):
    from app.cli import _purge_user
    db.session.add(HealthLegalHold(owner_id=world.a_id, categories=["coach"], matter_ref="M",
                                   created_at=datetime.utcnow(),
                                   review_at=datetime.utcnow() + timedelta(days=1)))
    db.session.commit()
    _purge_user(db.session.get(User, world.a_id))
    db.session.commit()
    assert HealthLegalHold.query.count() == 0


def test_run_pending_expires_old_records_and_reports_overdue(world, caplog):
    _withdraw(world.a_id)
    old = HealthErasure.query.filter_by(owner_id=world.a_id).one()
    old.requested_at = datetime.utcnow() - timedelta(days=31)
    db.session.add(HealthErasure(owner_id=999999, generation=1,
                                 requested_at=datetime.utcnow() - timedelta(days=1200),
                                 completed_at=datetime.utcnow() - timedelta(days=1100)))
    db.session.commit()
    world.s3.failing.add(world.a_keys["meal"])
    results = health_erasure.run_pending()
    assert results["failed"] == 1 and results["records_expired"] == 1 and results["overdue"] == 1
    assert "event=overdue" in caplog.text
    assert "left knee" not in caplog.text and "celiac" not in caplog.text


def test_health_erasure_is_in_daily_maintenance(monkeypatch, app):
    from app.jobs import tasks
    called = []
    monkeypatch.setattr(health_erasure, "run_pending", lambda: called.append(1) or {})
    with app.app_context():
        tasks.run_daily_maintenance(datetime.utcnow().isoformat())
    assert called == [1]
