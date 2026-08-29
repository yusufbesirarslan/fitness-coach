"""User silindiğinde çocuk satırların DB CASCADE ile temizlendiğini doğrular.

passive_deletes=True (app/models.py) + SQLite FK enforcement (app/extensions.py
_enforce_sqlite_foreign_keys) → dev/test cascade davranışı prod Postgres ile aynı.
FK pragma kapalıyken bu test öksüz satır bırakır, passive_deletes olmadan ORM
NOT NULL FK'yi NULL'lamaya çalışıp patlardı.

    python -m pytest tests/test_cascade_delete.py -v
"""
from app.extensions import db
from app.models import (DailyActivity, MealLog, PumpCheck,
                        PumpCheckComparison, PumpCheckComparisonRequest,
                        Supplement, User, WaterLog, WorkoutLog)
from app.timeutil import day_key


def test_user_delete_cascades_children(app, make_user):
    user = make_user("cascade_user")
    uid = user.id
    db.session.add_all([
        WorkoutLog(user_id=uid, exercise_name="Bench", sets=3, reps=10, weight_kg=60, volume=1800),
        Supplement(user_id=uid, product_name="Kreatin", brand="X"),
        MealLog(user_id=uid, ogun="Öğle", yemekler="tavuk", tarih=day_key()),
        DailyActivity(user_id=uid, steps=1000, intensity="moderate", date_key=day_key()),
        WaterLog(user_id=uid, date_key=day_key(), count=3),
        PumpCheck(user_id=uid, valid=True, date_key=day_key()),
    ])
    db.session.commit()

    # Çocukları ELLE temizlemeden doğrudan kullanıcıyı sil — DB CASCADE devralmalı.
    db.session.delete(user)
    db.session.commit()

    assert db.session.get(User, uid) is None
    for model in (WorkoutLog, Supplement, MealLog, DailyActivity, WaterLog, PumpCheck):
        assert model.query.filter_by(user_id=uid).count() == 0, model.__name__


def test_purge_user_covers_every_foreign_key_to_user():
    # Coverage follows the FK target, not a column-name convention. A future
    # reporter_id/mentioned_user_id must fail this guard.
    from app.cli import _USER_CHILD_MODELS, _USER_FK_MANUAL_CLEANUP
    direct = set(_USER_CHILD_MODELS)
    missing = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if cls.__name__ == "User":
            continue
        for column in mapper.columns:
            targets_user = any(
                fk.column.table.name == "user" for fk in column.foreign_keys
            )
            if not targets_user:
                continue
            if column.name == "user_id" and cls in direct:
                continue
            if (cls.__name__, column.name) in _USER_FK_MANUAL_CLEANUP:
                continue
            missing.append(f"{cls.__name__}.{column.name}")
    assert missing == [], f"_purge_user kapsamında olmayan user FK'leri: {missing}"


def test_purge_user_removes_social_and_wearable_children(app, make_user):
    from app.cli import _purge_user
    from app.models import (CognitoSession, PumpCheckComment, PumpCheckLike,
                            UserWearableConnection)
    user, friend = make_user("purge_me"), make_user("friend_of_purged")
    uid = user.id
    pc = PumpCheck(user_id=uid, valid=True, date_key=day_key())
    db.session.add(pc)
    db.session.flush()
    pc_id = pc.id
    db.session.add_all([
        CognitoSession(session_id="sid-purge", user_id=uid, cognito_username="purge_me",
                       access_token="enc", refresh_token="enc"),
        PumpCheckLike(pump_check_id=pc_id, user_id=friend.id),
        PumpCheckComment(pump_check_id=pc_id, user_id=friend.id, body="nice pump"),
        UserWearableConnection(user_id=uid, provider="whoop",
                               access_token_encrypted="enc",
                               refresh_token_encrypted="enc"),
    ])
    db.session.commit()

    _purge_user(user)
    db.session.commit()

    assert CognitoSession.query.filter_by(user_id=uid).count() == 0
    assert UserWearableConnection.query.filter_by(user_id=uid).count() == 0
    # Kullanıcının pump check'i silinince ona bağlı like/comment de kalmamalı.
    assert PumpCheckLike.query.filter_by(pump_check_id=pc_id).count() == 0
    assert PumpCheckComment.query.filter_by(pump_check_id=pc_id).count() == 0


def test_user_delete_cascades_challenge_rows(app, make_user):
    # Sprint 5 PR3: UserChallengeProgress + UserBadge user CASCADE ile temizlenir;
    # Challenge (user_id yok, katalog) korunur.
    from app.models import Challenge, UserBadge, UserChallengeProgress
    user = make_user("ch_cascade")
    uid = user.id
    c = Challenge(code="weekly_workouts", title="X", metric="workout_logged",
                  target_value=3, xp_reward=100, challenge_type="global", period_type="weekly")
    db.session.add(c)
    db.session.flush()
    db.session.add_all([
        UserChallengeProgress(user_id=uid, challenge_id=c.id, period_key="2026-W29", progress=1),
        UserBadge(user_id=uid, badge_code="pump_week"),
    ])
    db.session.commit()

    db.session.delete(user)
    db.session.commit()

    assert db.session.get(User, uid) is None
    assert UserChallengeProgress.query.filter_by(user_id=uid).count() == 0
    assert UserBadge.query.filter_by(user_id=uid).count() == 0
    assert db.session.get(Challenge, c.id) is not None  # katalog parent korunur


def test_comparison_delete_cascades_ledger_but_never_source_pump_checks(
        app, make_user):
    user = make_user("comparison_cascade")
    baseline = PumpCheck(user_id=user.id, valid=True, date_key="2026-08-12")
    current = PumpCheck(user_id=user.id, valid=True, date_key="2026-08-13")
    db.session.add_all([baseline, current])
    db.session.flush()
    comparison = PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="C" * 24,
        analysis_version="pump-check-comparison-analysis/v1",
    )
    db.session.add(comparison)
    db.session.flush()
    request = PumpCheckComparisonRequest(
        user_id=user.id,
        idempotency_key="comparison-cascade-key",
        fingerprint="f" * 64,
        comparison_id=comparison.id,
    )
    db.session.add(request)
    db.session.commit()
    baseline_id, current_id = baseline.id, current.id
    comparison_id, request_id = comparison.id, request.id

    db.session.delete(comparison)
    db.session.commit()

    assert db.session.get(PumpCheckComparisonRequest, request_id) is None
    assert db.session.get(PumpCheckComparison, comparison_id) is None
    assert db.session.get(PumpCheck, baseline_id) is not None
    assert db.session.get(PumpCheck, current_id) is not None


def test_source_delete_cascades_comparison_and_request_only(app, make_user):
    user = make_user("comparison_source_cascade")
    baseline = PumpCheck(user_id=user.id, valid=True, date_key="2026-08-12")
    current = PumpCheck(user_id=user.id, valid=True, date_key="2026-08-13")
    db.session.add_all([baseline, current])
    db.session.flush()
    comparison = PumpCheckComparison(
        user_id=user.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="D" * 24,
        analysis_version="pump-check-comparison-analysis/v1",
    )
    db.session.add(comparison)
    db.session.flush()
    request = PumpCheckComparisonRequest(
        user_id=user.id,
        idempotency_key="source-cascade-key",
        fingerprint="e" * 64,
        comparison_id=comparison.id,
    )
    db.session.add(request)
    db.session.commit()
    baseline_id, current_id = baseline.id, current.id
    comparison_id, request_id = comparison.id, request.id

    db.session.delete(baseline)
    db.session.commit()

    assert db.session.get(PumpCheck, baseline_id) is None
    assert db.session.get(PumpCheck, current_id) is not None
    assert db.session.get(PumpCheckComparison, comparison_id) is None
    assert db.session.get(PumpCheckComparisonRequest, request_id) is None


def test_delete_user_removes_comparison_records_without_other_sources(
        app, make_user):
    from app.cli import _purge_user

    owner = make_user("comparison_erasure")
    other_user = make_user("comparison_bystander")
    baseline = PumpCheck(user_id=owner.id, valid=True, date_key="2026-08-12")
    current = PumpCheck(user_id=owner.id, valid=True, date_key="2026-08-13")
    other_check = PumpCheck(
        user_id=other_user.id, valid=True, date_key="2026-08-13")
    db.session.add_all([baseline, current, other_check])
    db.session.flush()
    comparison = PumpCheckComparison(
        user_id=owner.id,
        baseline_pump_check_id=baseline.id,
        current_pump_check_id=current.id,
        public_id="E" * 24,
        analysis_version="pump-check-comparison-analysis/v1",
    )
    db.session.add(comparison)
    db.session.flush()
    request = PumpCheckComparisonRequest(
        user_id=owner.id,
        idempotency_key="comparison-erasure-key",
        fingerprint="d" * 64,
        comparison_id=comparison.id,
    )
    db.session.add(request)
    db.session.commit()
    owner_id = owner.id
    comparison_id, request_id = comparison.id, request.id
    other_check_id = other_check.id

    _purge_user(owner)
    db.session.commit()

    assert db.session.get(User, owner_id) is None
    assert db.session.get(PumpCheckComparisonRequest, request_id) is None
    assert db.session.get(PumpCheckComparison, comparison_id) is None
    assert db.session.get(PumpCheck, other_check_id) is not None


def test_purge_user_removes_feed_v2_rows_both_directions(app, make_user):
    from app.cli import _purge_user
    from app.models import (FeedHide, FeedItem, FeedItemComment, FeedItemLike,
                            FeedReport)
    user, friend = make_user("feed_purge"), make_user("feed_friend")
    uid, fid = user.id, friend.id
    my_check = PumpCheck(user_id=uid, valid=True, date_key=day_key())
    friend_check = PumpCheck(user_id=fid, valid=True, date_key=day_key())
    db.session.add_all([my_check, friend_check])
    db.session.flush()
    my_check_id, friend_check_id = my_check.id, friend_check.id
    # user's OWN repost of friend's check + friend's repost of user's check
    own_item = FeedItem(user_id=uid, item_type="repost", ref_type="pump_check", ref_id=friend_check_id)
    cross_item = FeedItem(user_id=fid, item_type="quote", ref_type="pump_check", ref_id=my_check_id, body="x")
    db.session.add_all([own_item, cross_item])
    db.session.flush()
    own_id, cross_id = own_item.id, cross_item.id
    db.session.add_all([
        FeedItemLike(feed_item_id=cross_id, user_id=uid),        # user liked friend's quote of user's check
        FeedItemComment(feed_item_id=cross_id, user_id=uid, body="c"),
        FeedHide(user_id=uid, target_type="pump_check", target_id=friend_check_id),
        FeedReport(user_id=uid, target_type="feed_item", target_id=own_id, reason="spam"),
    ])
    db.session.commit()

    _purge_user(user)
    db.session.commit()

    assert db.session.get(FeedItem, own_id) is None            # user's own repost gone
    assert db.session.get(FeedItem, cross_id) is None          # cross-user repost of user's check gone
    assert FeedItemLike.query.filter_by(user_id=uid).count() == 0
    assert FeedItemComment.query.filter_by(user_id=uid).count() == 0
    assert FeedHide.query.filter_by(user_id=uid).count() == 0
    assert FeedReport.query.filter_by(user_id=uid).count() == 0
