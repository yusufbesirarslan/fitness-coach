"""Route tests for the gamification blueprint + leaderboard service paths.

Redis'siz ortamda (conftest) leaderboard her zaman Postgres yoluna düşer —
sıralama/tiebreak, friends kapsamı, listede-olmayan kullanıcının rütbesi ve
haftalık ödül pop-up uçları sabitlenir.

    python -m pytest tests/test_gamification_routes.py -v
"""
from app.extensions import db
from app.models import Friendship, User, WeeklyWinner


# ---------------------------------------------------------------------------
# /leaderboard/data — Postgres yolu
# ---------------------------------------------------------------------------

def test_leaderboard_orders_by_xp_then_streak(client, auth_user, make_users_bulk):
    make_users_bulk(1, prefix="low", rank_points=100, streak_count=1)
    make_users_bulk(1, prefix="high", rank_points=500, streak_count=2)
    make_users_bulk(1, prefix="tie", rank_points=100, streak_count=9)

    body = client.get("/leaderboard/data").get_json()
    names = [e["username"] for e in body["entries"]]
    assert names[:3] == ["high0", "tie0", "low0"]  # XP desc, eşitlikte streak desc
    assert body["in_list"] is True                  # auth_user 0 XP ile de listede
    assert body["me"]["username"] == "testuser"


def test_leaderboard_weekly_timeframe_uses_weekly_xp(client, auth_user, make_users_bulk):
    make_users_bulk(1, prefix="alltime", rank_points=9000, weekly_xp=0)
    make_users_bulk(1, prefix="thisweek", rank_points=10, weekly_xp=300)

    body = client.get("/leaderboard/data?timeframe=weekly").get_json()
    assert body["entries"][0]["username"] == "thisweek0"
    assert body["entries"][0]["xp"] == 300          # gösterilen XP haftalık
    assert body["entries"][0]["level"] == 1         # seviye ömür-boyu XP'den


def test_leaderboard_me_outside_top100_gets_rank(client, auth_user, make_users_bulk):
    make_users_bulk(101, rank_points=1000)
    body = client.get("/leaderboard/data").get_json()
    assert len(body["entries"]) == 100
    assert body["in_list"] is False
    assert body["me"]["rank"] == 102


def test_leaderboard_friends_scope_empty(client, auth_user):
    body = client.get("/leaderboard/data?scope=friends").get_json()
    assert body["no_friends"] is True
    assert body["entries"] == []


def test_leaderboard_friends_scope_includes_self_and_friends(client, auth_user, make_users_bulk):
    friend, stranger = make_users_bulk(2, prefix="kisi", rank_points=700)
    db.session.add(Friendship(sender_id=auth_user.id, receiver_id=friend.id, status="accepted"))
    db.session.commit()

    body = client.get("/leaderboard/data?scope=friends").get_json()
    names = {e["username"] for e in body["entries"]}
    assert names == {"kisi0", "testuser"}  # yabancı (kisi1) listede değil
    assert body["entries"][0]["username"] == "kisi0"


# ---------------------------------------------------------------------------
# Haftalık ödül pop-up'ı
# ---------------------------------------------------------------------------

def test_reward_check_empty(client, auth_user):
    assert client.get("/leaderboard/reward-check").get_json() == {"show": False}


def test_reward_check_and_dismiss_cycle(client, auth_user):
    db.session.add(WeeklyWinner(user_id=auth_user.id, week_key="2026-W23",
                                rank=1, xp_awarded=500, notified=False))
    db.session.commit()

    body = client.get("/leaderboard/reward-check").get_json()
    assert body == {"show": True, "rank": 1, "xp": 500, "week": "2026-W23"}

    assert client.post("/leaderboard/reward-dismiss").get_json() == {"ok": True}
    assert client.get("/leaderboard/reward-check").get_json() == {"show": False}
    db.session.expire_all()
    assert db.session.get(User, auth_user.id).last_reward_week == "2026-W23"


def test_reward_dismiss_records_newest_week(client, auth_user):
    # D3: birden çok görülmemiş kazançta last_reward_week EN YENİ haftaya
    # ayarlanmalı (sırasız sorgu eski haftayı seçebiliyordu). Sığ→derin sırada ekle.
    db.session.add(WeeklyWinner(user_id=auth_user.id, week_key="2026-W24",
                                rank=2, xp_awarded=300, notified=False))
    db.session.add(WeeklyWinner(user_id=auth_user.id, week_key="2026-W22",
                                rank=1, xp_awarded=500, notified=False))
    db.session.commit()

    assert client.post("/leaderboard/reward-dismiss").get_json() == {"ok": True}
    db.session.expire_all()
    assert db.session.get(User, auth_user.id).last_reward_week == "2026-W24"


def test_reward_check_not_leaked_to_other_user(client, auth_user, make_users_bulk):
    other = make_users_bulk(1)[0]
    db.session.add(WeeklyWinner(user_id=other.id, week_key="2026-W23",
                                rank=1, xp_awarded=500, notified=False))
    db.session.commit()
    assert client.get("/leaderboard/reward-check").get_json() == {"show": False}


# ---------------------------------------------------------------------------
# Sayfalar
# ---------------------------------------------------------------------------

def test_leaderboard_and_quests_pages_render(client, auth_user):
    from app.models import DailyQuest
    db.session.add(DailyQuest(title="Daily Login", points_reward=10, quest_type="login"))
    db.session.commit()
    assert client.get("/leaderboard").status_code == 200
    assert client.get("/quests").status_code == 200
