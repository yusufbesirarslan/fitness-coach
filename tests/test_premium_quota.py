"""Freemium AI-plan haftalık kota kapısı (app/services/premium.py + route'lar).

Ücretsiz kullanıcı plan-türü başına Istanbul haftasında 1 ÜRETİM yapabilir;
premium sınırsız. Başarısız üretim (5xx) hakkı yakmaz. Kota config ile
kapatılabilir (testlerde varsayılan kapalı).

    python -m pytest tests/test_premium_quota.py -v
"""
import json
from datetime import date

import pytest
from flask_login import login_user
from sqlalchemy.exc import IntegrityError

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import User, UserSession
from app.services import premium

PLAN_JSON = {
    "program": [{"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
                 "sure_dk": 45, "tahmini_kalori": 380, "egzersizler": []}],
    "haftalik_ozet": {"toplam_antrenman_gun": 3, "toplam_tahmini_kalori": 1200,
                      "yogunluk_skoru": 8, "denge_skoru": 8, "uygunluk_skoru": 9},
}


PLAN_JSON["program"] = [
    {"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
     "sure_dk": 45, "tahmini_kalori": 380,
     "egzersizler": [{"isim": "Lat Pulldown", "set": 3, "tekrar": "10-12",
                      "dinlenme": "75 sn", "not": "kontrollü"}]},
    {"gun": "Salı", "tip": "dinlenme", "odak": "Aktif Toparlanma",
     "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
    {"gun": "Çarşamba", "tip": "antrenman", "odak": "Full Body",
     "sure_dk": 45, "tahmini_kalori": 360,
     "egzersizler": [{"isim": "Goblet Squat", "set": 3, "tekrar": "8-12",
                      "dinlenme": "90 sn", "not": "RPE 7"}]},
    {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma",
     "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
    {"gun": "Cuma", "tip": "antrenman", "odak": "Push Pull",
     "sure_dk": 45, "tahmini_kalori": 370,
     "egzersizler": [{"isim": "Seated Row", "set": 3, "tekrar": "10-12",
                      "dinlenme": "75 sn", "not": "omuzları düşür"}]},
    {"gun": "Cumartesi", "tip": "dinlenme", "odak": "Aktif Toparlanma",
     "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
    {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma",
     "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
]


def _mock_plan(monkeypatch):
    raw = json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kw: raw)


@pytest.fixture
def quota_on(app):
    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    return app


@pytest.fixture
def training_session(auth_user):
    db.session.add(UserSession(user_id=auth_user.id, goal="kilo verme",
                               fitness_level="beginner", current_activity="active",
                               tdee=2700))
    db.session.commit()
    return auth_user


# ---------------------------------------------------------------------------
# Yardımcı birim testleri
# ---------------------------------------------------------------------------

def test_week_key_format():
    assert premium._week_key(date(2026, 6, 15)) == "2026-W25"
    assert premium._week_key(date(2026, 1, 1)) == "2026-W01"


def test_remaining_and_reserve_per_kind(make_user):
    u = make_user("kotauser")
    assert premium.remaining_ai_plans(u, "training") == 1
    assert premium.reserve_ai_quota(u, "training", 1) is True
    assert premium.remaining_ai_plans(u, "training") == 0
    # Beslenme türü bağımsız sayılır.
    assert premium.remaining_ai_plans(u, "nutrition") == 1


def test_premium_user_is_unlimited(make_user):
    u = make_user("vip", is_premium=True)
    assert premium.remaining_ai_plans(u, "training") is None
    assert premium.reserve_ai_quota(u, "training", 1) is True
    assert premium.remaining_ai_plans(u, "training") is None


def test_new_week_resets_quota(make_user):
    u = make_user("rollover")
    u.user_metadata = {"ai_plan_quota": {"week": "2000-W01", "training": 5}}
    db.session.commit()
    # Geçen haftaya ait sayaç düşer → bu hafta tekrar 1 hak.
    assert premium.remaining_ai_plans(u, "training") == 1


def test_reserve_ai_quota_rejects_stale_user_after_last_allowance(make_user):
    user = make_user("reserve")
    stale_user = User(id=user.id, user_metadata={})

    assert premium.reserve_ai_quota(user, "training", 1) is True
    assert premium.reserve_ai_quota(stale_user, "training", 1) is False
    assert premium.remaining_ai_plans(user, "training") == 0


def test_refund_ai_quota_has_zero_floor(make_user):
    user = make_user("refund-floor")

    premium.refund_ai_quota(user, "training")

    fresh_meta = db.session.query(User.user_metadata).filter_by(id=user.id).scalar()
    assert fresh_meta["ai_plan_quota"]["training"] == 0


def test_reservation_week_returns_week_captured_by_reserve(make_user, monkeypatch):
    user = make_user("reservation-week")
    monkeypatch.setattr(premium, "_week_key", lambda d=None: "2026-W27")

    assert premium.reserve_ai_quota(user, "training", 1) is True

    assert premium.reservation_week(user, "training") == "2026-W27"


def test_refund_does_not_decrement_rollover_reservation(make_user, monkeypatch):
    user = make_user("week-boundary")
    monkeypatch.setattr(premium, "_week_key", lambda d=None: "2026-W27")
    assert premium.reserve_ai_quota(user, "training", 1) is True
    reserved_week = premium.reservation_week(user, "training")

    db.session.query(User).filter_by(id=user.id).update({
        User.user_metadata: {
            "ai_plan_quota": {"week": "2026-W28", "training": 1},
        },
    })
    db.session.commit()

    premium.refund_ai_quota(user, "training", reserved_week=reserved_week)

    fresh_meta = db.session.query(User.user_metadata).filter_by(id=user.id).scalar()
    assert fresh_meta["ai_plan_quota"]["training"] == 1


# ---------------------------------------------------------------------------
# Route entegrasyonu
# ---------------------------------------------------------------------------

def test_second_generation_blocked_with_402(client, quota_on, training_session, monkeypatch):
    _mock_plan(monkeypatch)
    assert client.post("/training-plan", json={}).status_code == 200
    blocked = client.post("/training-plan", json={})
    assert blocked.status_code == 402
    assert blocked.get_json()["premium_required"] is True


def test_premium_user_bypasses_route_gate(client, quota_on, training_session, monkeypatch):
    training_session.is_premium = True
    db.session.commit()
    _mock_plan(monkeypatch)
    for _ in range(3):
        assert client.post("/training-plan", json={}).status_code == 200


def test_failed_generation_does_not_consume_quota(client, quota_on, training_session, monkeypatch):
    # Geçersiz LLM çıktısı → 500; hak yanmamalı.
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kw: "bu json değil")
    assert client.post("/training-plan", json={}).status_code == 500
    _mock_plan(monkeypatch)
    assert client.post("/training-plan", json={}).status_code == 200


def test_quota_disabled_allows_repeat(client, training_session, monkeypatch):
    # Varsayılan (kota kapalı): tekrar üretim engellenmez.
    _mock_plan(monkeypatch)
    assert client.post("/training-plan", json={}).status_code == 200
    assert client.post("/training-plan", json={}).status_code == 200


def test_failed_generation_refunds_pre_call_reservation(
        client, quota_on, training_session, monkeypatch):
    used_during_call = []

    def invalid_plan(**kwargs):
        fresh_meta = db.session.query(User.user_metadata).filter_by(
            id=training_session.id).scalar() or {}
        quota = fresh_meta.get("ai_plan_quota") or {}
        used_during_call.append(quota.get("training", 0))
        return "not json"

    monkeypatch.setattr(training_bp, "_heavy_chat", invalid_plan)

    assert client.post("/training-plan", json={}).status_code == 500
    # The initial generation and its one retry share the same reservation.
    assert used_during_call == [1, 1]
    assert premium.remaining_ai_plans(training_session, "training") == 1


def test_failed_generation_preserves_response_when_refund_fails(
        client, quota_on, training_session, monkeypatch):
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "not json")
    monkeypatch.setattr(
        premium, "refund_ai_quota",
        lambda user, counter_key: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    response = client.post("/training-plan", json={})

    assert response.status_code == 500


def test_plan_gate_refunds_when_wrapped_route_raises(app, make_user):
    user = make_user("route-exception")

    @premium.premium_ai_plan_gate("training")
    def raising_route():
        raise RuntimeError("provider failed")

    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    with app.test_request_context("/training-plan", method="POST"):
        login_user(user)
        with pytest.raises(RuntimeError, match="provider failed"):
            raising_route()

    assert premium.remaining_ai_plans(user, "training") == 1


@pytest.mark.parametrize(
    ("status", "should_refund"),
    [(200, False), (201, False), (204, False), (302, False),
     (400, True), (500, True)],
)
def test_plan_gate_refunds_only_http_failures(
        app, make_user, monkeypatch, status, should_refund):
    user = make_user(f"route-status-{status}")
    refunds = []
    monkeypatch.setattr(premium, "reserve_ai_quota", lambda *args: True)
    monkeypatch.setattr(premium, "refund_ai_quota",
                        lambda *args, **kwargs: refunds.append(args[1]))

    @premium.premium_ai_plan_gate("training")
    def status_route():
        return "", status

    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    with app.test_request_context("/training-plan", method="POST"):
        login_user(user)
        response = status_route()

    assert response.status_code == status
    assert refunds == (["training"] if should_refund else [])


def test_plan_gate_rolls_back_failed_transaction_before_refund(app, make_user):
    user = make_user("failed-transaction")

    @premium.premium_ai_plan_gate("training")
    def route_with_failed_flush():
        db.session.add(User(
            username=user.username,
            email="duplicate@example.com",
            password_hash="unused",
        ))
        db.session.flush()

    app.config["AI_PLAN_QUOTA_ENABLED"] = True
    with app.test_request_context("/training-plan", method="POST"):
        login_user(user)
        with pytest.raises(IntegrityError):
            route_with_failed_flush()

    assert premium.remaining_ai_plans(user, "training") == 1
