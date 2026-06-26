"""Tests for the nudge engine (analytics_engine.py).

AI Coach'a enjekte edilen proaktif dürtmelerin eşiklerini sabitler: 48 saatlik
log boşluğu, riskteki seri, haftalık protein hedefi, rapor günü.

    python -m pytest tests/test_analytics_engine.py -v
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from analytics_engine import _check_streak_at_risk, _check_weekly_report_day, get_nudges
from app.extensions import db
from app.models import (MealLog, User, UserSession, WaterLog, WeeklyCheckIn,
                        WorkoutLog)
from app.timeutil import app_today

MODELS = {
    "WorkoutLog": WorkoutLog,
    "MealLog": MealLog,
    "UserSession": UserSession,
    "WeeklyCheckIn": WeeklyCheckIn,
    "WaterLog": WaterLog,
}


def _nudge_types(user):
    return {n.split(":")[0] for n in get_nudges(user, db, MODELS)}


# ---------------------------------------------------------------------------
# 48 saatlik log boşluğu
# ---------------------------------------------------------------------------

def test_no_logs_at_all_triggers_missing_logs(make_user):
    user = make_user("alice", last_login=date.today())
    assert "NUDGE_MISSING_LOGS" in _nudge_types(user)


def test_only_workout_logged_nudges_nutrition(make_user):
    user = make_user("bob", last_login=date.today())
    db.session.add(WorkoutLog(user_id=user.id, exercise_name="squat", sets=3, reps=5))
    db.session.commit()
    types = _nudge_types(user)
    assert "NUDGE_NO_NUTRITION" in types
    assert "NUDGE_MISSING_LOGS" not in types


def test_only_nutrition_logged_nudges_workout(make_user):
    user = make_user("carol", last_login=date.today())
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="muz", kalori=100))
    db.session.commit()
    types = _nudge_types(user)
    assert "NUDGE_NO_WORKOUT" in types
    assert "NUDGE_MISSING_LOGS" not in types


def test_recent_logs_silence_missing_log_nudges(make_user):
    user = make_user("dave", last_login=date.today())
    db.session.add(WorkoutLog(user_id=user.id, exercise_name="squat", sets=3, reps=5))
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="muz", kalori=100))
    db.session.commit()
    types = _nudge_types(user)
    assert not types & {"NUDGE_MISSING_LOGS", "NUDGE_NO_WORKOUT", "NUDGE_NO_NUTRITION"}


def test_stale_logs_count_as_missing(make_user):
    user = make_user("eve", last_login=date.today())
    old = datetime.utcnow() - timedelta(hours=49)
    db.session.add(WorkoutLog(user_id=user.id, exercise_name="squat",
                              sets=3, reps=5, created_at=old))
    db.session.commit()
    assert "NUDGE_MISSING_LOGS" in _nudge_types(user)


# ---------------------------------------------------------------------------
# Riskteki seri — bugün henüz giriş yapılmadıysa ve seri >= 5 ise uyar.
# ---------------------------------------------------------------------------

def test_streak_at_risk_thresholds():
    yesterday = date.today() - timedelta(days=1)

    nudges = []
    _check_streak_at_risk(SimpleNamespace(streak_count=5, last_login=yesterday), date.today(), nudges)
    assert any(n.startswith("NUDGE_STREAK_RISK") for n in nudges)

    nudges = []
    _check_streak_at_risk(SimpleNamespace(streak_count=4, last_login=yesterday), date.today(), nudges)
    assert nudges == []  # seri eşiğin altında

    nudges = []
    _check_streak_at_risk(SimpleNamespace(streak_count=9, last_login=date.today()), date.today(), nudges)
    assert nudges == []  # bugün zaten aktif


def test_streak_risk_uses_prev_last_login_when_provided():
    # update_streak before_request hook last_login'i "bugün" yapar; get_nudges'a
    # ÖNCEKI değer (dün) geçilince seri-riski dürtüsü yine de tetiklenmeli (F3).
    today = date.today()
    yesterday = today - timedelta(days=1)

    nudges = []
    _check_streak_at_risk(SimpleNamespace(streak_count=6, last_login=today),
                          today, nudges, prev_last_login=yesterday)
    assert any(n.startswith("NUDGE_STREAK_RISK") for n in nudges)

    # Gün içi sonraki istekte prev de bugün olur → tetiklenmez.
    nudges = []
    _check_streak_at_risk(SimpleNamespace(streak_count=6, last_login=today),
                          today, nudges, prev_last_login=today)
    assert nudges == []


# ---------------------------------------------------------------------------
# Haftalık protein hedefi — hedef kalorinin %30'u / 4 kcal * 7 gün, %90 eşiği.
# ---------------------------------------------------------------------------

def test_protein_goal_nudge_at_90_percent(make_user):
    user = make_user("frank", last_login=date.today())
    # kas kazanma → %30 protein: haftalık hedef 1050 g; %90 = 945 g (F8).
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="tavuk",
                           kalori=4000, protein=950))
    db.session.commit()
    assert "NUDGE_PROTEIN_GOAL" in _nudge_types(user)


def test_protein_goal_silent_below_90_percent(make_user):
    user = make_user("grace", last_login=date.today())
    db.session.add(UserSession(user_id=user.id, target_calories=2000))
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="tavuk",
                           kalori=2000, protein=500))
    db.session.commit()
    assert "NUDGE_PROTEIN_GOAL" not in _nudge_types(user)


def test_protein_goal_needs_a_session_with_target(make_user):
    user = make_user("heidi", last_login=date.today())
    assert "NUDGE_PROTEIN_GOAL" not in _nudge_types(user)


def test_protein_goal_ignores_meals_outside_current_week(make_user):
    # B2/A7: pencere kanonik MealLog.tarih ile hesaplanır. Bu haftanın dışındaki
    # (14 gün önce) yüksek-protein öğünü toplama GİRMEMELİ → nudge tetiklenmez.
    user = make_user("ivan", last_login=date.today())
    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    old_day = (app_today() - timedelta(days=14)).isoformat()
    db.session.add(MealLog(user_id=user.id, ogun="öğün", yemekler="tavuk",
                           kalori=4000, protein=950, tarih=old_day))
    db.session.commit()
    assert "NUDGE_PROTEIN_GOAL" not in _nudge_types(user)


# ---------------------------------------------------------------------------
# Haftalık rapor günü — Pazartesi (0) ve Pazar (6).
# ---------------------------------------------------------------------------

def test_weekly_report_only_on_monday_and_sunday():
    def fires(d):
        nudges = []
        _check_weekly_report_day(d, nudges)
        return bool(nudges)

    assert fires(date(2026, 6, 8)) is True    # Pazartesi
    assert fires(date(2026, 6, 14)) is True   # Pazar
    assert fires(date(2026, 6, 10)) is False  # Çarşamba


# ---------------------------------------------------------------------------
# Toparlanma sinyalleri — son check-in'de uyku <=2 veya yorgunluk >=4.
# ---------------------------------------------------------------------------

def test_recovery_nudge_on_poor_sleep_or_high_fatigue(make_user):
    user = make_user("ivan", last_login=date.today())
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=2,
                                 fatigue=3, progressive_overload="evet"))
    db.session.commit()
    assert "NUDGE_RECOVERY" in _nudge_types(user)


def test_recovery_nudge_silent_when_recovered(make_user):
    user = make_user("judy", last_login=date.today())
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=4,
                                 fatigue=2, progressive_overload="evet"))
    db.session.commit()
    assert "NUDGE_RECOVERY" not in _nudge_types(user)


# ---------------------------------------------------------------------------
# Progresif yüklenme duraksaması — en güncel check-in'de "hayir".
# ---------------------------------------------------------------------------

def test_overload_stall_nudge(make_user):
    user = make_user("mallory", last_login=date.today())
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=4,
                                 fatigue=2, progressive_overload="hayir"))
    db.session.commit()
    assert "NUDGE_OVERLOAD_STALL" in _nudge_types(user)


def test_overload_stall_uses_latest_checkin(make_user):
    user = make_user("niaj", last_login=date.today())
    old = datetime.utcnow() - timedelta(days=7)
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=4, fatigue=2,
                                 progressive_overload="hayir", created_at=old))
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=4, fatigue=2,
                                 progressive_overload="evet"))
    db.session.commit()
    assert "NUDGE_OVERLOAD_STALL" not in _nudge_types(user)


# ---------------------------------------------------------------------------
# Hidrasyon — son 7 günde ortalama günlük su < 6 bardak.
# ---------------------------------------------------------------------------

def test_low_hydration_nudge_below_threshold(make_user):
    user = make_user("olivia", last_login=date.today())
    today = app_today()
    for i in range(3):
        db.session.add(WaterLog(user_id=user.id, count=3,
                                date_key=(today - timedelta(days=i)).isoformat()))
    db.session.commit()
    assert "NUDGE_LOW_HYDRATION" in _nudge_types(user)


def test_hydration_silent_when_adequate(make_user):
    user = make_user("peggy", last_login=date.today())
    today = app_today()
    for i in range(3):
        db.session.add(WaterLog(user_id=user.id, count=8,
                                date_key=(today - timedelta(days=i)).isoformat()))
    db.session.commit()
    assert "NUDGE_LOW_HYDRATION" not in _nudge_types(user)


def test_hydration_silent_without_records(make_user):
    user = make_user("rupert", last_login=date.today())
    assert "NUDGE_LOW_HYDRATION" not in _nudge_types(user)


def test_new_nudges_disabled_when_models_absent(make_user):
    # Eski çağıran (yalnızca 3 model) WeeklyCheckIn/WaterLog geçmezse yeni
    # dürtüler sessizce devre dışı kalmalı (geriye dönük uyum).
    user = make_user("sybil", last_login=date.today())
    db.session.add(WeeklyCheckIn(user_id=user.id, weight=80, uyku_kalitesi=1,
                                 fatigue=5, progressive_overload="hayir"))
    db.session.commit()
    legacy = {"WorkoutLog": WorkoutLog, "MealLog": MealLog, "UserSession": UserSession}
    types = {n.split(":")[0] for n in get_nudges(user, db, legacy)}
    assert "NUDGE_RECOVERY" not in types
    assert "NUDGE_OVERLOAD_STALL" not in types
