"""Route tests for the tracking blueprint (app/blueprints/tracking.py).

Kilo logu/karşılaştırma mesajları, haftalık check-in (AI mock'lu), kilo
güncelleme (BMR/TDEE yeniden hesabı + günlük check-in upsert), adım/aktivite
logu ve dashboard nudge çevirileri.

    python -m pytest tests/test_tracking_routes.py -v
"""
import pytest

from app.blueprints import tracking as tracking_bp
from app.extensions import db
from app.models import DailyActivity, User, UserSession, WeeklyCheckIn, WeeklyLog
from app.timeutil import day_key


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
    gained_msg = gained.get_json()["message"]
    assert "1.5 kg aldın" in gained_msg
    assert "kaydedildi" in gained_msg          # D6: kilo alma dalında önek düşmemeli

    same = client.post("/log", json={"weight": 80})
    assert "aynı kilo" in same.get_json()["message"]


def test_log_rejects_non_scalar_weight(client, auth_user):
    # D5: liste/dict gibi JSON tipleri float()'ta TypeError verir → 500 yerine 400.
    assert client.post("/log", json={"weight": [80]}).status_code == 400
    assert client.post("/log", json={"weight": {"v": 80}}).status_code == 400

@pytest.mark.parametrize("path", ["/log", "/checkin", "/update-weight"])
@pytest.mark.parametrize("weight", [-5, 19.9, 500.1, 5000, "NaN", "Infinity"])
def test_weight_routes_reject_out_of_range_values(
        client, auth_user, fake_checkin_feedback, path, weight):
    profile_weight = auth_user.weight
    log_count = WeeklyLog.query.filter_by(user_id=auth_user.id).count()
    checkin_count = WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count()

    response = client.post(path, json={"weight": weight})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Kilo 20 ile 500 kg aras\u0131nda olmal\u0131d\u0131r"
    }
    db.session.expire_all()
    assert WeeklyLog.query.filter_by(user_id=auth_user.id).count() == log_count
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == checkin_count
    assert db.session.get(User, auth_user.id).weight == profile_weight


@pytest.mark.parametrize("path", ["/log", "/checkin", "/update-weight"])
def test_weight_routes_reject_oversized_integer_without_mutation(
        app, client, auth_user, fake_checkin_feedback, path):
    # Convert the uncaught OverflowError into the route's real HTTP 500 response
    # so RED is a behavioral 500-vs-400 failure rather than a propagated error.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    profile_weight = auth_user.weight
    log_count = WeeklyLog.query.filter_by(user_id=auth_user.id).count()
    checkin_count = WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count()

    response = client.post(path, json={"weight": 10 ** 309})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Kilo 20 ile 500 kg aras\u0131nda olmal\u0131d\u0131r"
    }
    db.session.expire_all()
    assert WeeklyLog.query.filter_by(user_id=auth_user.id).count() == log_count
    assert WeeklyCheckIn.query.filter_by(user_id=auth_user.id).count() == checkin_count
    assert db.session.get(User, auth_user.id).weight == profile_weight


@pytest.mark.parametrize("path", ["/log", "/checkin", "/update-weight"])
@pytest.mark.parametrize("weight", [20, 500])
def test_weight_routes_accept_inclusive_boundaries(
        client, auth_user, fake_checkin_feedback, path, weight):
    assert client.post(path, json={"weight": weight}).status_code == 200


def test_weight_range_error_uses_authenticated_user_language(
        client, auth_user):
    auth_user.language = "en"
    db.session.commit()

    response = client.post("/log", json={"weight": 19.9})

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Weight must be between 20 and 500 kg"
    }


@pytest.mark.parametrize("path", ["/log", "/checkin", "/update-weight"])
def test_weight_routes_distinguish_missing_and_nonnumeric_values(
        client, auth_user, path):
    missing = client.post(path, json={})
    nonnumeric = client.post(path, json={"weight": "abc"})

    assert missing.status_code == nonnumeric.status_code == 400
    assert missing.get_json() == {"error": "Kilo zorunludur"}
    assert nonnumeric.get_json() == {
        "error": "Kilo say\u0131sal olmal\u0131d\u0131r"
    }

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
    # Sayısal olmayan/boş steps 500 ValueError fırlatmamalı → _to_int ile 0'a düşer
    # ve "pozitif olmalı" 400 guard'ına yakalanır.
    assert client.post("/api/activity/log",
                       json={"steps": "abc", "intensity": "moderate"}).status_code == 400
    assert client.post("/api/activity/log",
                       json={"steps": "", "intensity": "moderate"}).status_code == 400


def test_activity_log_and_today_aggregation(client, complete_user):
    first = client.post("/api/activity/log", json={"steps": 10_000, "intensity": "moderate"})
    assert first.get_json()["calories_burned"] > 0

    # M3: "bugünün aktivitesi" TEK satırdır. Aynı gün tekrar loglamak — yoğunluk
    # değişse bile — yeni satır EKLEMEZ, günün satırını DEĞİŞTİRİR. Eskiden
    # yoğunluk başına ayrı satır açılıp today_activity'de kaloriler/adımlar
    # TOPLANARAK çift/üç sayılıyordu (yoğunluk menüsünü değiştirmek kalori ekliyordu).
    client.post("/api/activity/log", json={"steps": 12_000, "intensity": "moderate"})
    last = client.post("/api/activity/log", json={"steps": 2_000, "intensity": "brisk"})
    assert DailyActivity.query.filter_by(user_id=complete_user.id).count() == 1

    today = client.get("/api/activity/today").get_json()
    # Toplama YOK: yalnızca en son loglanan tek satır yansır.
    assert today["total_steps"] == 2_000
    assert len(today["entries"]) == 1
    assert today["entries"][0]["intensity"] == "brisk"
    assert today["total_calories"] == last.get_json()["calories_burned"]


def test_daily_activity_defaults_to_today_key(auth_user):
    entry = DailyActivity(user_id=auth_user.id)
    db.session.add(entry)
    db.session.commit()

    assert entry.date_key == day_key()


def test_daily_activity_unique_per_day_regardless_of_intensity(auth_user):
    # C4: "günde tam bir satır" invariantını DB de zorlar. Eski kısıt intensity
    # içerdiğinden farklı yoğunluklu iki eşzamanlı istek İKİ satır bırakabiliyordu
    # (handler'ın sil-ekle'si çakışma üretmeden); artık ikinci INSERT reddedilir
    # ve log_daily_activity'nin IntegrityError-upsert dalı gerçekten tetiklenir.
    from sqlalchemy.exc import IntegrityError
    db.session.add(DailyActivity(user_id=auth_user.id, steps=1000,
                                 intensity="light", date_key="2026-07-04"))
    db.session.commit()
    db.session.add(DailyActivity(user_id=auth_user.id, steps=2000,
                                 intensity="brisk", date_key="2026-07-04"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


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
