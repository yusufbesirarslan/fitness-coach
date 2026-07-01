"""Freemium AI-plan haftalık kota kapısı (app/services/premium.py + route'lar).

Ücretsiz kullanıcı plan-türü başına Istanbul haftasında 1 ÜRETİM yapabilir;
premium sınırsız. Başarısız üretim (5xx) hakkı yakmaz. Kota config ile
kapatılabilir (testlerde varsayılan kapalı).

    python -m pytest tests/test_premium_quota.py -v
"""
import json
from datetime import date

import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import UserSession
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


def test_remaining_and_record_per_kind(make_user):
    u = make_user("kotauser")
    assert premium.remaining_ai_plans(u, "training") == 1
    premium.record_ai_plan_generation(u, "training")
    assert premium.remaining_ai_plans(u, "training") == 0
    # Beslenme türü bağımsız sayılır.
    assert premium.remaining_ai_plans(u, "nutrition") == 1


def test_premium_user_is_unlimited(make_user):
    u = make_user("vip", is_premium=True)
    assert premium.remaining_ai_plans(u, "training") is None
    premium.record_ai_plan_generation(u, "training")     # no-op
    assert premium.remaining_ai_plans(u, "training") is None


def test_new_week_resets_quota(make_user):
    u = make_user("rollover")
    u.user_metadata = {"ai_plan_quota": {"week": "2000-W01", "training": 5}}
    db.session.commit()
    # Geçen haftaya ait sayaç düşer → bu hafta tekrar 1 hak.
    assert premium.remaining_ai_plans(u, "training") == 1


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
