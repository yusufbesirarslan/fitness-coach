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
