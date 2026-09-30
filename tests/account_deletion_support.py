"""Shared fakes and fixtures for the LP-11 account-deletion tests.

Only the two external boundaries are faked — Cognito (`cognito_service`,
`cognito_jwt`) and the S3 client (`s3_helper._get_client`). Everything else is
the real code against a real database: the opaque mobile credential pipeline,
the owner-checked S3 helpers, `_purge_user`, the foreign keys.

A fake Cognito identity is `sub-<username>` (`FakeCognito.rebind` models a
new sign-up: same username, new subject); its tokens are `access|<sub>`,
`id|<sub>`, `refresh|<sub>`. Like the real provider, `DeleteUser` is authorized
by the access token alone and deletes the token's own principal; a deleted
identity can no longer sign in, but its already-issued access token still
passes OFFLINE signature validation until it expires — exactly the property
that makes local session removal load-bearing.
"""
import calendar
import uuid
from datetime import datetime, timedelta

from botocore.exceptions import ClientError

import s3_helper
from app.extensions import db
from app.models import (
    Activity, CoachConversation, CoachMessage, CognitoSession, CustomMeal,
    CustomMealItem, DailyActivity, FeedHide, FeedItem, FeedItemComment,
    FeedItemLike, FeedReport, Friendship, MealLog, MealPhotoCleanup, Message,
    MobileAccessCredential, MobileAuthSession, MobileRefreshCredential,
    Notification, NutritionPlan, PendingAction, PumpCheck, PumpCheckComment,
    PumpCheckComparison, PumpCheckComparisonRequest, PumpCheckLike, Supplement,
    TrainingPlan, TrainingPlanGenerationOperation, User, UserBadge,
    UserSession, UserWearableConnection, WaterLog, WearableActivityLog,
    WearableSleepLog, WearableWorkoutLog, WeeklyCheckIn, WeeklyLog,
    WeeklyWinner, WorkoutLog, WorkoutSession,
)
from app.services import cognito_jwt, cognito_service, mobile_auth


BUCKET = "lp11-test-bucket"


class FakeCognito:
    def __init__(self):
        self.live = set()
        self.delete_calls = []
        self.delete_failure = None
        # username -> subject, when it is not the default `sub-<username>`.
        self.subjects = {}

    def register(self, user):
        self.live.add(user.cognito_sub)

    def rebind(self, username, sub):
        """The provider now answers `username` with a NEW subject — the same
        person signing up again after their identity was deleted."""
        self.subjects[username] = sub
        self.live.add(sub)

    def authenticate(self, username, password):
        sub = self.subjects.get(username, f"sub-{username}")
        if sub not in self.live:
            raise cognito_service.CognitoServiceError(
                "Kullanıcı adı veya şifre hatalı.", "NotAuthorizedException")
        return {"tokens": {
            "access_token": f"access|{sub}", "id_token": f"id|{sub}",
            "refresh_token": f"refresh|{sub}", "expires_in": 3600},
            "claims": {"sub": sub}}

    def delete_user(self, access_token):
        self.delete_calls.append(access_token)
        if self.delete_failure is not None:
            raise self.delete_failure
        sub = access_token.split("|", 1)[1]
        if sub not in self.live:
            raise cognito_service.CognitoServiceError(
                "Kullanıcı adı veya şifre hatalı.", "UserNotFoundException")
        self.live.discard(sub)

    @property
    def deleted_subs(self):
        return [token.split("|", 1)[1] for token in self.delete_calls]


def validate_token(token, expected_use, leeway_seconds=0):
    sub = token.split("|", 1)[1]
    if expected_use == "id":
        # `sub-alice` and a re-issued `sub2-alice` both belong to `alice`.
        username = sub.split("-", 1)[1]
        return {"sub": sub, "email": f"{username}@example.com",
                "email_verified": True, "cognito:username": username}
    return {"sub": sub, "exp": calendar.timegm(
        (datetime.utcnow() + timedelta(hours=1)).timetuple())}


class FakeS3:
    """The S3 client `s3_helper` builds; records DeleteObject calls."""

    def __init__(self):
        self.objects = set()
        self.deleted = []
        self.failing = set()
        self.missing_error = set()

    def delete_object(self, Bucket, Key):
        assert Bucket == BUCKET
        if Key in self.failing:
            raise ClientError(
                {"Error": {"Code": "InternalError",
                           "Message": f"boom {Key} secret-provider-text"}},
                "DeleteObject")
        if Key in self.missing_error:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "gone"}},
                "DeleteObject")
        self.deleted.append(Key)
        self.objects.discard(Key)


def install_fakes(monkeypatch, s3_enabled=True):
    cognito, s3 = FakeCognito(), FakeS3()
    monkeypatch.setattr(cognito_service, "authenticate", cognito.authenticate)
    monkeypatch.setattr(cognito_service, "delete_user", cognito.delete_user)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate_token)
    monkeypatch.setattr(s3_helper, "S3_BUCKET_NAME", BUCKET if s3_enabled else "")
    monkeypatch.setattr(s3_helper, "_get_client", lambda: s3)
    return cognito, s3


def issue_bearer(cognito, user):
    """A real opaque mobile session for `user` (only Cognito is faked)."""
    cognito.register(user)
    issued = mobile_auth.login(user.username, "Sifre123")
    return {"Authorization": f"Bearer {issued.access_credential}"}


def object_key(prefix, user_id):
    return f"{prefix}/{user_id}/2026/09/{uuid.uuid4().hex}.jpg"


def seed_account(user, s3, tag):
    """Give `user` a row in (almost) every user-owned domain plus media.

    Returns `(own, keys)`: `own` is a list of `(Model, id)` for rows that
    belong to this user alone, `keys` the storage keys minted for it.
    """
    uid = user.id
    keys = {
        "avatar": object_key("avatars", uid),
        "pump": object_key("pump-checks", uid),
        "pump2": object_key("pump-checks", uid),
        "meal": object_key("meals", uid),
        "meal_pending": object_key("meals", uid),
    }
    s3.objects.update(keys.values())
    user.profile_picture_key = keys["avatar"]
    user.full_name = f"Private Name {tag}"

    pump = PumpCheck(user_id=uid, valid=True, date_key="2026-09-01",
                     image_key=keys["pump"], description=f"private {tag}")
    pump2 = PumpCheck(user_id=uid, valid=True, date_key="2026-09-02",
                      image_key=keys["pump2"])
    conversation = CoachConversation(user_id=uid)
    custom = CustomMeal(user_id=uid, meal_name=f"meal {tag}",
                        date_key="2026-09-01")
    meal = MealLog(user_id=uid, ogun="Öğle", yemekler=f"private food {tag}",
                   tarih="2026-09-01", photo_key=keys["meal"])
    rows = [
        pump, pump2, conversation, custom, meal,
        UserSession(user_id=uid, target_calories=2100),
        WeeklyLog(user_id=uid, weight=80.0),
        WeeklyCheckIn(user_id=uid, weight=79.5, yogunluk=3),
        NutritionPlan(user_id=uid, plan_data="{}"),
        TrainingPlan(user_id=uid, plan_data="{}"),
        TrainingPlanGenerationOperation(
            user_id=uid, idempotency_key=f"gen-{tag}",
            request_fingerprint="a" * 64, status="FAILED"),
        PendingAction(user_id=uid, action_type="log_meal", payload="{}"),
        Activity(user_id=uid, activity_type="workout", content=f"did {tag}"),
        Supplement(user_id=uid, product_name="Kreatin", brand="X"),
        WaterLog(user_id=uid, date_key="2026-09-01", count=3),
        WorkoutLog(user_id=uid, exercise_name="Bench", sets=3, reps=10,
                   weight_kg=60, volume=1800),
        WorkoutSession(user_id=uid,
                       workout_date=datetime(2026, 9, 1).date()),
        DailyActivity(user_id=uid, steps=1000, date_key="2026-09-01"),
        UserBadge(user_id=uid, badge_code=f"badge-{tag}"),
        WeeklyWinner(user_id=uid, week_key="2026-W35", rank=1,
                     xp_awarded=10),
        UserWearableConnection(user_id=uid, provider="whoop",
                               access_token_encrypted="enc"),
        WearableSleepLog(user_id=uid, provider="whoop",
                         source_id=f"s-{tag}", date_key="2026-09-01"),
        WearableActivityLog(user_id=uid, provider="whoop",
                            date_key="2026-09-01"),
        WearableWorkoutLog(user_id=uid, provider="whoop",
                           source_id=f"w-{tag}", date_key="2026-09-01"),
        CognitoSession(session_id=f"web-{tag}", user_id=uid,
                       cognito_username=user.username,
                       access_token="enc", refresh_token="enc"),
        Notification(user_id=uid, ntype="system"),
    ]
    db.session.add_all(rows)
    db.session.flush()
    comparison = PumpCheckComparison(
        user_id=uid, baseline_pump_check_id=pump.id,
        current_pump_check_id=pump2.id, public_id=(tag * 24)[:24],
        analysis_version="pump-check-comparison-analysis/v1")
    db.session.add(comparison)
    db.session.flush()
    extra = [
        comparison,
        PumpCheckComparisonRequest(
            user_id=uid, idempotency_key=f"cmp-{tag}", fingerprint="f" * 64,
            comparison_id=comparison.id),
        CoachMessage(conversation_id=conversation.id, role="user",
                     content=f"my private question {tag}"),
        CustomMealItem(custom_meal_id=custom.id, food_name="rice", grams=100),
        MealPhotoCleanup(user_id=uid, entry_id=meal.id + 1000,
                         photo_key=keys["meal_pending"], entry_revision=1,
                         diary_date="2026-08-31"),
        FeedItem(user_id=uid, item_type="repost", ref_type="activity",
                 ref_id=999999),
        FeedHide(user_id=uid, target_type="activity", target_id=999999),
    ]
    db.session.add_all(extra)
    db.session.commit()
    own = [(type(row), row.id) for row in rows + extra]
    return own, keys


def seed_cross_links(a, b):
    """Edges between two accounts; purging A removes the ones touching A."""
    a_pump = PumpCheck.query.filter_by(user_id=a.id).first()
    b_pump = PumpCheck.query.filter_by(user_id=b.id).first()
    b_repost_of_a = FeedItem(user_id=b.id, item_type="quote",
                             ref_type="pump_check", ref_id=a_pump.id, body="q")
    db.session.add(b_repost_of_a)
    db.session.flush()
    db.session.add_all([
        Friendship(sender_id=a.id, receiver_id=b.id, status="accepted"),
        Message(sender_id=a.id, receiver_id=b.id, body="hi from a"),
        PumpCheckLike(pump_check_id=a_pump.id, user_id=b.id),
        PumpCheckComment(pump_check_id=a_pump.id, user_id=b.id, body="nice"),
        PumpCheckLike(pump_check_id=b_pump.id, user_id=a.id),
        FeedItemLike(feed_item_id=b_repost_of_a.id, user_id=a.id),
        FeedItemComment(feed_item_id=b_repost_of_a.id, user_id=a.id,
                        body="thanks"),
        FeedReport(user_id=a.id, target_type="pump_check",
                   target_id=b_pump.id, reason="spam"),
        Notification(user_id=b.id, actor_id=a.id, ntype="like"),
    ])
    b.referred_by_id = a.id
    db.session.commit()
    return b_repost_of_a.id


def user_fk_columns():
    for mapper in db.Model.registry.mappers:
        for column in mapper.columns:
            if any(fk.column.table.name == "user"
                   for fk in column.foreign_keys):
                yield mapper.class_, column


def census(user_id):
    """Rows naming `user_id` in ANY user foreign key, per (model, column)."""
    return {
        (cls.__name__, column.name):
            db.session.query(cls).filter(column == user_id).count()
        for cls, column in user_fk_columns()
    }


def session_rows(user_id):
    """Live and dead mobile/web session material for one account."""
    family_ids = [row.id for row in MobileAuthSession.query.filter_by(
        user_id=user_id)]
    return {
        "families": len(family_ids),
        "access": MobileAccessCredential.query.filter(
            MobileAccessCredential.session_id.in_(family_ids or [-1])).count(),
        "refresh": MobileRefreshCredential.query.filter(
            MobileRefreshCredential.session_id.in_(family_ids or [-1])).count(),
        "web": CognitoSession.query.filter_by(user_id=user_id).count(),
    }


def rows_exist(own):
    return [(model.__name__, row_id) for model, row_id in own
            if db.session.get(model, row_id) is None]


def rows_gone(own):
    return [(model.__name__, row_id) for model, row_id in own
            if db.session.get(model, row_id) is not None]


def user_exists(user_id):
    return db.session.get(User, user_id) is not None
