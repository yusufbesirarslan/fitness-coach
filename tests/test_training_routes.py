"""Route tests for the training blueprint (app/blueprints/training.py).

AI plan üretimi (mock LLM — JSON ayıklama/skor fallback'leri), plan kaydetme,
Pump Check kapılı antrenman tamamlama ve su sayacı.

    python -m pytest tests/test_training_routes.py -v
"""
import json
from datetime import date

import pytest

from app.blueprints import training as training_bp
from app.extensions import db
from app.models import DailyQuest, PumpCheck, TrainingPlan, UserQuestProgress, UserSession, WaterLog, WorkoutLog
from tests.test_validators import _image_data_url

PLAN_JSON = {
    "program": [{"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
                 "sure_dk": 45, "tahmini_kalori": 380, "egzersizler": []}],
    "haftalik_ozet": {"toplam_antrenman_gun": 3, "toplam_tahmini_kalori": 1200,
                      "yogunluk_skoru": 8, "denge_skoru": 8, "uygunluk_skoru": 9},
}


def _seven_day_program(first_exercise="Lat Pulldown", include_safe_leg_press=False):
    first_exercises = [
        {"isim": first_exercise, "set": 3, "tekrar": "10-12",
         "dinlenme": "75 sn", "not": "kontrollü"}
    ]
    if include_safe_leg_press:
        first_exercises.append(
            {"isim": "Leg Press", "set": 3, "tekrar": "10",
             "dinlenme": "90 sn", "not": "kontrollü"}
        )
    return [
        {"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
         "sure_dk": 45, "tahmini_kalori": 380, "egzersizler": first_exercises},
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


PLAN_JSON["program"] = _seven_day_program()
PLAN_JSON["haftalik_ozet"]["toplam_tahmini_kalori"] = 1110


@pytest.fixture
def with_session(auth_user):
    db.session.add(UserSession(user_id=auth_user.id, goal="kilo verme",
                               fitness_level="beginner", current_activity="active",
                               tdee=2700))
    db.session.commit()
    return auth_user


# ---------------------------------------------------------------------------
# /training-plan (AI üretim)
# ---------------------------------------------------------------------------

def test_plan_requires_existing_session(client, auth_user):
    response = client.post("/training-plan", json={})
    assert response.status_code == 400
    assert "Önce" in response.get_json()["error"]


def test_plan_parses_fenced_json_and_scores(client, with_session, monkeypatch):
    raw = "```json\n" + json.dumps(PLAN_JSON, ensure_ascii=False) + "\n```"
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: raw)

    body = client.post("/training-plan", json={"gun_sayisi": 3}).get_json()
    assert body["program"] == PLAN_JSON["program"]
    assert body["overall_score"] == 8.3      # (8+8+9)/3
    assert body["score_label"] == "İyi"


def test_plan_zero_scores_default_to_seven(client, with_session, monkeypatch):
    plan = dict(PLAN_JSON, haftalik_ozet={"yogunluk_skoru": 0, "denge_skoru": 0,
                                          "uygunluk_skoru": 0})
    monkeypatch.setattr(training_bp, "_heavy_chat",
                        lambda **kwargs: json.dumps(plan, ensure_ascii=False))
    body = client.post("/training-plan", json={}).get_json()
    assert body["overall_score"] == 7.0
    assert body["score_label"] == "Orta"


def test_plan_invalid_llm_output_returns_500(client, with_session, monkeypatch):
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "tabii, işte planın:")
    assert client.post("/training-plan", json={}).status_code == 500


def test_plan_prompt_includes_cardio_preferences(client, with_session, monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    client.post("/training-plan", json={"kardiyo_tipi": "kosu", "kardiyo_gun": 2})
    assert "koşu" in captured["prompt"]
    assert "Haftada 2 gün kardiyo" in captured["prompt"]


# ---------------------------------------------------------------------------
# Sakatlık (injuries) — kalıcılık, katı direktif, post-filtre güvenlik ağı
# ---------------------------------------------------------------------------

# Bel fıtığı için kontrendike (Deadlift) + güvenli (Leg Press) içeren plan.
INJURY_PLAN = {
    "program": [{"gun": "Pazartesi", "tip": "antrenman", "odak": "Full",
                 "sure_dk": 45, "tahmini_kalori": 380, "egzersizler": [
                     {"isim": "Conventional Deadlift", "set": 4, "tekrar": "5",
                      "dinlenme": "120 sn", "not": "ağır kaldır"},
                     {"isim": "Leg Press", "set": 3, "tekrar": "10",
                      "dinlenme": "90 sn", "not": "kontrollü"},
                 ]}],
    "haftalik_ozet": {"yogunluk_skoru": 8, "denge_skoru": 8, "uygunluk_skoru": 9},
}


INJURY_PLAN["program"] = _seven_day_program("Conventional Deadlift", include_safe_leg_press=True)


def test_plan_persists_posted_injuries(client, with_session, auth_user, monkeypatch):
    from app.models import User
    monkeypatch.setattr(training_bp, "_heavy_chat",
                        lambda **kwargs: json.dumps(PLAN_JSON, ensure_ascii=False))

    client.post("/training-plan", json={"injuries": "Menisküs"})

    db.session.expire_all()
    user = db.session.get(User, auth_user.id)
    assert (user.user_metadata or {}).get("injuries") == "Menisküs"


def test_plan_prompt_includes_strict_injury_directive(client, with_session, monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    client.post("/training-plan", json={"injuries": "bel fıtığı"})
    assert "KONTRENDİKASYON" in captured["prompt"]
    assert "deadlift" in captured["prompt"].lower()   # yasak hareket
    assert "Bird Dog" in captured["prompt"]            # güvenli alternatif


def test_plan_flags_contraindicated_exercise_that_slips_through(client, with_session, monkeypatch):
    monkeypatch.setattr(training_bp, "_heavy_chat",
                        lambda **kwargs: json.dumps(INJURY_PLAN, ensure_ascii=False))

    body = client.post("/training-plan", json={"injuries": "bel fıtığı"}).get_json()

    warnings = body["injury_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["egzersiz"] == "Conventional Deadlift"
    assert warnings[0]["neden"] == "deadlift"

    exs = body["program"][0]["egzersizler"]
    assert exs[0]["not"].startswith("⚠️ SAKATLIK RİSKİ")
    assert exs[1]["not"] == "kontrollü"               # güvenli egzersiz dokunulmadan kalır


def test_plan_without_injuries_is_unconstrained(client, with_session, monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    body = client.post("/training-plan", json={}).get_json()
    assert body["injury_warnings"] == []
    assert "KONTRENDİKASYON" not in captured["prompt"]


def test_plan_hicbiri_clears_stored_injuries(client, with_session, auth_user, monkeypatch):
    from app.models import User
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    client.post("/training-plan", json={"injuries": "Menisküs"})
    client.post("/training-plan", json={"injuries": "Hiçbiri"})

    db.session.expire_all()
    user = db.session.get(User, auth_user.id)
    assert (user.user_metadata or {}).get("injuries") == "Hiçbiri"
    assert "KONTRENDİKASYON" not in captured["prompt"]   # 'Hiçbiri' → kısıt yok


# ---------------------------------------------------------------------------
# Plan kaydet / aktif plan
# ---------------------------------------------------------------------------

def test_save_plan_replaces_previous(client, auth_user):
    assert client.post("/training-plan/save", json={}).status_code == 400

    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 7.0})
    client.post("/training-plan/save", json={"plan": {"v": 2}, "score": 8.0})
    plans = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(plans) == 1
    assert json.loads(plans[0].plan_data) == {"v": 2}


def test_active_plan_roundtrip(client, auth_user):
    assert client.get("/training-plan/active").get_json() == {"exists": False}
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 7.5})
    body = client.get("/training-plan/active").get_json()
    assert body["exists"] is True
    assert body["plan"] == {"v": 1}
    assert body["score"] == 7.5


# ---------------------------------------------------------------------------
# /workout/complete — Pump Check kapısı
# ---------------------------------------------------------------------------

@pytest.fixture
def workout_ready(client, auth_user):
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 7.0})
    db.session.add(DailyQuest(title="Log a Workout", points_reward=50,
                              quest_type="workout_logged"))
    db.session.commit()
    return auth_user


def test_complete_requires_plan(client, auth_user):
    response = client.post("/workout/complete", json={})
    assert response.status_code == 400
    assert "planın yok" in response.get_json()["error"]


def test_complete_requires_image(client, workout_ready):
    response = client.post("/workout/complete", json={})
    assert response.status_code == 400
    assert "fotoğraf" in response.get_json()["error"].lower()


def test_complete_rejected_by_ai_validation(client, workout_ready, monkeypatch):
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a: {"valid": False, "reason": "Spor ortamı görünmüyor."})
    response = client.post("/workout/complete",
                           json={"image": _image_data_url("JPEG"), "location_type": "ev"})
    assert response.status_code == 422
    assert PumpCheck.query.count() == 0


def test_complete_awards_xp_and_records_pump_check(client, workout_ready, monkeypatch):
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a: {"valid": True, "fallback": False})
    response = client.post("/workout/complete",
                           json={"image": _image_data_url("JPEG"), "location_type": "salon"})
    body = response.get_json()
    assert body["points_awarded"] == 10 + 25 + 50    # baz + pump + quest
    assert body["quest_awarded"]["xp"] == 50
    assert body["new_total"] == 10 + 25 + 50

    check = PumpCheck.query.filter_by(user_id=workout_ready.id).one()
    assert check.valid is True
    assert check.image_key is None                    # S3 kapalı (test env)
    assert check.date_key is not None                 # günlük idempotency anahtarı yazıldı

    # Faz B/F6: UI antrenman tamamlama artık kanonik WorkoutLog da yazar —
    # haftalık rapor ve "48 saattir antrenman yok" dürtüsü gerçek antrenmanı görsün.
    wlog = WorkoutLog.query.filter_by(user_id=workout_ready.id).one()
    assert wlog.exercise_name.startswith("Antrenman tamamlandı")

    # Aynı gün ikinci tamamlama reddedilir.
    again = client.post("/workout/complete",
                        json={"image": _image_data_url("JPEG")})
    assert again.status_code == 400
    assert "zaten" in again.get_json()["error"]


def test_pump_check_day_unique_constraint(client, auth_user):
    # uq_pump_check_day: aynı kullanıcı + gün için ikinci PumpCheck DB seviyesinde
    # reddedilir → eşzamanlı 'antrenmanı tamamla' yarışında çift XP'yi engeller.
    from sqlalchemy.exc import IntegrityError
    today = date.today().isoformat()
    db.session.add(PumpCheck(user_id=auth_user.id, valid=True, date_key=today))
    db.session.commit()
    db.session.add(PumpCheck(user_id=auth_user.id, valid=True, date_key=today))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_workout_status_flips_after_completion(client, workout_ready, monkeypatch):
    assert client.get("/workout/status").get_json() == {"completed": False}
    quest = DailyQuest.query.filter_by(quest_type="workout_logged").one()
    db.session.add(UserQuestProgress(user_id=workout_ready.id, quest_id=quest.id,
                                     date_key=date.today().isoformat(), is_claimed=True))
    db.session.commit()
    assert client.get("/workout/status").get_json() == {"completed": True}


# ---------------------------------------------------------------------------
# Su sayacı
# ---------------------------------------------------------------------------

def test_water_defaults_and_roundtrip(client, auth_user):
    assert client.get("/water").get_json() == {"count": 0, "goal": 8}

    assert client.post("/water", json={"count": 3}).get_json()["count"] == 3
    assert client.post("/water", json={"count": 99}).get_json()["count"] == 8   # üst kıs
    assert client.post("/water", json={"count": -2}).get_json()["count"] == 0   # alt kıs
    assert client.post("/water", json={"count": "abc"}).status_code == 400
    assert WaterLog.query.filter_by(user_id=auth_user.id).count() == 1          # upsert


def test_training_page_renders(client, auth_user):
    assert client.get("/training").status_code == 200


def test_plan_prompt_includes_deterministic_classification_and_style(client, with_session, monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(PLAN_JSON, ensure_ascii=False)
    monkeypatch.setattr(training_bp, "_heavy_chat", fake_chat)

    body = client.post("/training-plan", json={
        "gun_sayisi": 3,
        "antrenman_tarzi": "powerlifting",
    }).get_json()

    assert body["program"] == PLAN_JSON["program"]
    assert body["classification"]["level"] in {"Beginner", "Intermediate", "Advanced"}
    assert "Final classified level" in captured["prompt"]
    assert "LLM sınıflandırma yapmayacak" in captured["prompt"]
    assert "ana kaldırış" in captured["prompt"]
