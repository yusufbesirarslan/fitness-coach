"""Tests for the gamification service (app/services/gamification.py).

XP/seviye matematiği, günlük görev idempotency'si ve haftalık rollover'ın
(top-3 ödül + weekly_xp sıfırlama + tekrar-çalıştırma koruması) sabitlenmesi.
Redis yok (conftest REDIS_URL'i temizler) → leaderboard sync sessiz no-op.

    python -m pytest tests/test_gamification.py -v
"""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import DailyQuest, User, UserQuestProgress, WeeklyResetLog, WeeklyWinner
from app.services.gamification import (
    _last_completed_week_key,
    _lb_score,
    award_xp,
    complete_quest_for_user,
    get_level,
    get_title,
    run_weekly_rollover,
)


# ---------------------------------------------------------------------------
# Seviye / unvan matematiği
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("xp,level", [
    (None, 1), (0, 1), (499, 1), (500, 2), (999, 2), (5000, 11),
])
def test_level_thresholds(xp, level):
    assert get_level(xp) == level


@pytest.mark.parametrize("level,title", [
    (1, "Fitness Yolcusu"), (5, "Fitness Yolcusu"),
    (6, "Demir Bükücü"), (10, "Demir Bükücü"),
    (11, "Kas Mimarı"), (20, "Kas Mimarı"),
    (21, "FitX Efsanesi"), (50, "FitX Efsanesi"),
    (51, "Antrenman Tanrısı"),
])
def test_title_boundaries(level, title):
    assert get_title(level) == title


def test_lb_score_composite_and_streak_cap():
    # XP her zaman streak'i domine etmeli; streak 99999'da kırpılır.
    assert _lb_score(10, 3) == 10 * 100_000 + 3
    assert _lb_score(0, 1_000_000) == 99_999
    assert _lb_score(1, 0) > _lb_score(0, 99_999)
    assert _lb_score(None, None) == 0


# ---------------------------------------------------------------------------
# XP ödülü
# ---------------------------------------------------------------------------

def test_award_xp_updates_both_counters(make_user):
    user = make_user("alice", rank_points=100, weekly_xp=20)
    assert award_xp(user.id, 30) == 130
    assert user.rank_points == 130
    assert user.weekly_xp == 50


def test_award_xp_unknown_user_returns_none(app):
    assert award_xp(999_999, 10) is None


# ---------------------------------------------------------------------------
# Günlük görevler — günde bir kez, bilinmeyen tip sessizce None.
# ---------------------------------------------------------------------------

def test_complete_quest_awards_once_per_day(make_user):
    user = make_user("bob")
    db.session.add(DailyQuest(title="Daily Login", points_reward=10, quest_type="login"))
    db.session.commit()

    result = complete_quest_for_user(user.id, "login")
    assert result["awarded"] is True
    assert result["xp"] == 10
    assert result["new_total"] == 10
    assert result["level"] == get_level(10)

    assert complete_quest_for_user(user.id, "login") is None  # aynı gün ikinci kez
    assert UserQuestProgress.query.filter_by(user_id=user.id).count() == 1
    db.session.expire_all()
    assert db.session.get(User, user.id).rank_points == 10


def test_complete_quest_unknown_type_is_noop(make_user):
    user = make_user("carol")
    assert complete_quest_for_user(user.id, "boyle-gorev-yok") is None


# ---------------------------------------------------------------------------
# Haftalık rollover
# ---------------------------------------------------------------------------

def test_last_completed_week_key_boundaries():
    # Hafta sınırı Pazar 23:59 UTC. 8-14 Haziran 2026 = ISO 2026-W24.
    assert _last_completed_week_key(datetime(2026, 6, 10, 12, 0)) == "2026-W23"   # hafta ortası → önceki hafta
    assert _last_completed_week_key(datetime(2026, 6, 14, 23, 58)) == "2026-W23"  # kapanıştan 1 dk önce
    assert _last_completed_week_key(datetime(2026, 6, 14, 23, 59)) == "2026-W24"  # tam kapanış → bu hafta
    assert _last_completed_week_key(datetime(2026, 6, 15, 0, 0)) == "2026-W24"    # Pazartesi 00:00


def test_weekly_rollover_awards_top3_and_resets(make_user):
    first = make_user("first", weekly_xp=100, rank_points=1000)
    second = make_user("second", weekly_xp=50)
    third = make_user("third", weekly_xp=30)
    idle = make_user("idle", weekly_xp=0)  # 0 XP → kazanamaz

    result = run_weekly_rollover(force_week="2026-W23")
    assert result["status"] == "done"
    assert [w["user_id"] for w in result["winners"]] == [first.id, second.id, third.id]

    db.session.expire_all()
    # Ödül yalnızca all-time'a işler (500/300/150), weekly sayaçlar sıfırlanır.
    assert db.session.get(User, first.id).rank_points == 1500
    assert db.session.get(User, second.id).rank_points == 300
    assert db.session.get(User, third.id).rank_points == 150
    assert db.session.get(User, idle.id).rank_points == 0
    assert all(u.weekly_xp == 0 for u in User.query.all())

    assert WeeklyWinner.query.filter_by(week_key="2026-W23").count() == 3
    log = WeeklyResetLog.query.filter_by(week_key="2026-W23").one()
    assert log.winner_count == 3


def test_weekly_rollover_is_idempotent(make_user):
    user = make_user("first", weekly_xp=100)
    assert run_weekly_rollover(force_week="2026-W23")["status"] == "done"

    user.weekly_xp = 100  # yeni hafta XP'si gelmiş gibi
    db.session.commit()
    result = run_weekly_rollover(force_week="2026-W23")
    assert result["status"] == "already_processed"
    db.session.expire_all()
    assert db.session.get(User, user.id).rank_points == 500  # ikinci kez ödenmedi
    assert WeeklyWinner.query.count() == 1


def test_weekly_rollover_tiebreak_streak_then_id(make_user):
    low_streak = make_user("lowstreak", weekly_xp=100, streak_count=1)
    high_streak = make_user("highstreak", weekly_xp=100, streak_count=9)

    result = run_weekly_rollover(force_week="2026-W23")
    # Eşit XP'de yüksek streak önde; o da eşitse küçük id kazanırdı.
    assert [w["user_id"] for w in result["winners"]] == [high_streak.id, low_streak.id]
