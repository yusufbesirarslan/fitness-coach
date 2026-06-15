"""Tests for the nudge engine (analytics_engine.py).

AI Coach'a enjekte edilen proaktif dürtmelerin eşiklerini sabitler: 48 saatlik
log boşluğu, riskteki seri, haftalık protein hedefi, rapor günü.

    python -m pytest tests/test_analytics_engine.py -v
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from analytics_engine import _check_streak_at_risk, _check_weekly_report_day, get_nudges
from app.extensions import db
from app.models import MealLog, User, UserSession, WorkoutLog

MODELS = {
    "WorkoutLog": WorkoutLog,
    "MealLog": MealLog,
    "UserSession": UserSession,
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
