"""Route tests for the tracking blueprint (app/blueprints/tracking.py).

Kilo logu/karşılaştırma mesajları, haftalık check-in (AI mock'lu), kilo
güncelleme (BMR/TDEE yeniden hesabı + günlük check-in upsert), adım/aktivite
logu ve dashboard nudge çevirileri.

    python -m pytest tests/test_tracking_routes.py -v
"""
import pytest

from app.blueprints import tracking as tracking_bp
from app.extensions import db
from app.models import DailyActivity, UserSession, WeeklyCheckIn


@pytest.fixture
def complete_user(auth_user):
    auth_user.profile_complete = True
    auth_user.weight, auth_user.height, auth_user.age = 80, 180, 30
    auth_user.gender, auth_user.goal = "male", "kilo verme"
    auth_user.current_activity = "active"
    db.session.commit()
    return auth_user


# ---------------------------------------------------------------------------
# Ana sayfa
# ---------------------------------------------------------------------------

def test_home_redirects_to_setup_when_profile_incomplete(client, auth_user):
    response = client.get("/")
    assert response.status_code == 302
    assert "/setup" in response.headers["Location"]


def test_home_renders_for_complete_profile(client, complete_user):
    assert client.get("/").status_code == 200


# ---------------------------------------------------------------------------
# /log + /progress
# ---------------------------------------------------------------------------

def test_log_requires_numeric_weight(client, auth_user):
    assert client.post("/log", json={}).status_code == 400
    assert client.post("/log", json={"weight": "seksen"}).status_code == 400


def test_log_comparison_messages(client, auth_user):
    first = client.post("/log", json={"weight": 80, "note": "start"})
    assert "80.0 kg kaydedildi" in first.get_json()["message"]

    lost = client.post("/log", json={"weight": 78.5})
    assert "1.5 kg verdin" in lost.get_json()["message"]

    gained = client.post("/log", json={"weight": 80})
    assert "1.5 kg aldın" in gained.get_json()["message"]

    same = client.post("/log", json={"weight": 80})
    assert "aynı kilo" in same.get_json()["message"]


def test_progress_returns_logs_in_order(client, auth_user):
    client.post("/log", json={"weight": 80, "note": "ilk"})
    client.post("/log", json={"weight": 79})
    logs = client.get("/api/progress").get_json()
    assert [l["kilo"] for l in logs] == [80.0, 79.0]
    assert logs[0]["not"] == "ilk"


def test_progress_redirects_to_page(client, auth_user):
    res = client.get("/progress")
    assert res.status_code == 302
    assert "/progress-page" in res.headers["Location"]


# ---------------------------------------------------------------------------
# /checkin + /checkin-history
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_checkin_feedback(monkeypatch):
    monkeypatch.setattr(tracking_bp, "generate_checkin_feedback",
                        lambda *args, **kwargs: "mock geri bildirim")


def test_checkin_requires_weight(client, auth_user):
    assert client.post("/checkin", json={}).status_code == 400
    assert client.post("/checkin", json={"weight": "abc"}).status_code == 400


def test_checkin_saves_entry_with_feedback(client, auth_user, fake_checkin_feedback):
    response = client.post("/checkin", json={
        "weight": 79, "yogunluk": 4, "fatigue": 2,
        "progressive_overload": "evet", "uyku_kalitesi": 5,
        "beslenme_uyumu": 4, "note": "iyi hafta",
    })
    assert response.get_json()["coach_feedback"] == "mock geri bildirim"
    entry = WeeklyCheckIn.query.filter_by(user_id=auth_user.id).one()
    assert entry.progressive_overload == "evet"
    assert entry.coach_feedback == "mock geri bildirim"


def test_checkin_coerces_invalid_overload(client, auth_user, fake_checkin_feedback):
    client.post("/checkin", json={"weight": 79, "progressive_overload": "belki"})
    entry = WeeklyCheckIn.query.filter_by(user_id=auth_user.id).one()
    assert entry.progressive_overload == "kismen"


def test_checkin_history(client, auth_user, fake_checkin_feedback):
    client.post("/checkin", json={"weight": 80})
    client.post("/checkin", json={"weight": 79})
    rows = client.get("/checkin-history").get_json()
    assert [r["kilo"] for r in rows] == [80.0, 79.0]
    assert rows[0]["feedback"] == "mock geri bildirim"


# ---------------------------------------------------------------------------
# /update-weight — BMR yeniden hesabı + günlük check-in upsert
# ---------------------------------------------------------------------------

def test_update_weight_recalculates_and_upserts_checkin(client, complete_user):
    db.session.add(UserSession(user_id=complete_user.id, weight=80, bmr=1780,
                               tdee=2759, target_calories=2359))
    db.session.commit()

    response = client.post("/update-weight", json={"weight": 78})
    body = response.get_json()
    assert body["bmr"] == round(10 * 78 + 6.25 * 180 - 5 * 30 + 5)

    db.session.expire_all()
    session = UserSession.query.filter_by(user_id=complete_user.id).one()
    assert session.weight == 78.0

    # Aynı gün ikinci güncelleme yeni satır açmaz, mevcut check-in'i günceller.
    client.post("/update-weight", json={"weight": 77})
    checkins = WeeklyCheckIn.query.filter_by(user_id=complete_user.id).all()
    assert len(checkins) == 1
    assert checkins[0].weight == 77.0


def test_update_weight_validation(client, complete_user):
    assert client.post("/update-weight", json={}).status_code == 400
    assert client.post("/update-weight", json={"weight": "x"}).status_code == 400


# ---------------------------------------------------------------------------
# Günlük aktivite
# ---------------------------------------------------------------------------

def test_activity_log_validation(client, complete_user):
    assert client.post("/api/activity/log",
                       json={"steps": 100, "intensity": "turbo"}).status_code == 400
    assert client.post("/api/activity/log",
                       json={"steps": 0, "intensity": "moderate"}).status_code == 400


def test_activity_log_and_today_aggregation(client, complete_user):
    first = client.post("/api/activity/log", json={"steps": 10_000, "intensity": "moderate"})
    assert first.get_json()["calories_burned"] > 0

    # Aynı gün aynı yoğunluk → güncelleme (yeni satır yok).
    client.post("/api/activity/log", json={"steps": 12_000, "intensity": "moderate"})
    client.post("/api/activity/log", json={"steps": 2_000, "intensity": "brisk"})
    assert DailyActivity.query.filter_by(user_id=complete_user.id).count() == 2

    today = client.get("/api/activity/today").get_json()
    assert today["total_steps"] == 14_000
    assert len(today["entries"]) == 2


# ---------------------------------------------------------------------------
# Geçmiş / son oturum / nudge'lar
# ---------------------------------------------------------------------------

def test_history_returns_latest_sessions(client, auth_user):
    for w in (82, 81, 80):
        db.session.add(UserSession(user_id=auth_user.id, weight=w, coach_reply="r"))
    db.session.commit()
    rows = client.get("/history").get_json()
    assert len(rows) == 3
    assert {r["kilo"] for r in rows} == {80.0, 81.0, 82.0}


def test_last_session_roundtrip(client, complete_user):
    assert client.get("/last-session").get_json() == {"exists": False}

    db.session.add(UserSession(user_id=complete_user.id, weight=80, height=180,
                               age=30, gender="male", goal="kilo verme",
                               bmr=1780, tdee=2759, target_calories=2359))
    db.session.commit()
    body = client.get("/last-session").get_json()
    assert body["exists"] is True
    assert body["weight"] == 80          # current_user.weight öncelikli
    assert body["target_calories"] == 2359


def test_dashboard_nudges_translated(client, auth_user):
    body = client.get("/dashboard-nudges").get_json()
    # Hiç log yok → 48 saat nudge'ı Türkçe çevirisiyle döner.
    assert any("48 saatte" in n for n in body["nudges"])
    assert not any(n.startswith("NUDGE_") for n in body["nudges"])


def test_progress_page_renders(client, auth_user):
    assert client.get("/progress-page").status_code == 200
