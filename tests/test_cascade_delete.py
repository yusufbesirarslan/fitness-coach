"""User silindiğinde çocuk satırların DB CASCADE ile temizlendiğini doğrular.

passive_deletes=True (app/models.py) + SQLite FK enforcement (app/extensions.py
_enforce_sqlite_foreign_keys) → dev/test cascade davranışı prod Postgres ile aynı.
FK pragma kapalıyken bu test öksüz satır bırakır, passive_deletes olmadan ORM
NOT NULL FK'yi NULL'lamaya çalışıp patlardı.

    python -m pytest tests/test_cascade_delete.py -v
"""
from app.extensions import db
from app.models import (DailyActivity, MealLog, PumpCheck, Supplement, User,
                        WaterLog, WorkoutLog)
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


def test_purge_user_covers_every_user_child_model():
    # B4: cleanup-test-users'ın elle silme listesi user_id taşıyan HER modeli
    # içermeli — SQLite'ta FK pragma bir gün kapanırsa/eski DB'de cascade yoksa
    # öksüz satır kalmasın. Yeni user-child model ekleyen bu listeye de eklemeli.
    from app.cli import _USER_CHILD_MODELS
    covered = set(_USER_CHILD_MODELS)
    missing = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if cls.__name__ == "User":
            continue
        if any(c.name == "user_id" for c in mapper.columns) and cls not in covered:
            missing.append(cls.__name__)
    assert not missing, f"_purge_user kapsamında olmayan user-child modeller: {missing}"


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
