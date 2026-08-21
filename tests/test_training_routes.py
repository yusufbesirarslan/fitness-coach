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
from tests.test_workout_state import assert_valid_state_contract

PLAN_JSON = {
    "program": [{"gun": "Pazartesi", "tip": "antrenman", "odak": "Sırt",
                 "sure_dk": 45, "tahmini_kalori": 380, "egzersizler": []}],
    "haftalik_ozet": {"toplam_antrenman_gun": 3, "toplam_tahmini_kalori": 1200,
                      "yogunluk_skoru": 8, "denge_skoru": 8, "uygunluk_skoru": 9},
}


def _seven_day_program(first_exercise="Lat Pulldown", include_safe_leg_press=False):
    first_exercises = [
        {"isim": first_exercise, "set": 3, "tekrar": "10-12",
         "dinlenme": "75 sn", "not": "kontrollü"},
        {"isim": "Seated Row", "set": 3, "tekrar": "10-12",
         "dinlenme": "75 sn", "not": "kontrollü"},
        {"isim": "Push-up", "set": 3, "tekrar": "8-12",
         "dinlenme": "60 sn", "not": "kontrollü"},
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
         "egzersizler": [
             {"isim": "Goblet Squat", "set": 3, "tekrar": "8-12",
              "dinlenme": "90 sn", "not": "RPE 7"},
             {"isim": "Hip Hinge Drill", "set": 3, "tekrar": "8-12",
              "dinlenme": "90 sn", "not": "RPE 7"},
             {"isim": "Walking Lunge", "set": 3, "tekrar": "10",
              "dinlenme": "75 sn", "not": "kontrollü"},
         ]},
        {"gun": "Perşembe", "tip": "dinlenme", "odak": "Aktif Toparlanma",
         "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        {"gun": "Cuma", "tip": "antrenman", "odak": "Push Pull",
         "sure_dk": 45, "tahmini_kalori": 370,
         "egzersizler": [
             {"isim": "Seated Row", "set": 3, "tekrar": "10-12",
              "dinlenme": "75 sn", "not": "omuzları düşür"},
             {"isim": "Shoulder Press", "set": 3, "tekrar": "8-10",
              "dinlenme": "90 sn", "not": "kontrollü"},
             {"isim": "Lat Pulldown", "set": 3, "tekrar": "10-12",
              "dinlenme": "75 sn", "not": "kontrollü"},
         ]},
        {"gun": "Cumartesi", "tip": "dinlenme", "odak": "Aktif Toparlanma",
         "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
        {"gun": "Pazar", "tip": "dinlenme", "odak": "Aktif Toparlanma",
         "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []},
    ]


PLAN_JSON["program"] = _seven_day_program()
PLAN_JSON["haftalik_ozet"]["toplam_tahmini_kalori"] = 1110


def _expect_canonicalized(program):
    """Sprint 11 PR4 Task 3: /training-plan now canonicalizes every generated
    exercise reference against the server-owned catalog before returning it —
    isim becomes the catalog's canonical display name and exercise_id is
    added. Uses the same resolver the production code uses (already covered
    exhaustively in tests/test_sprint11_exercise_authority.py) as the oracle,
    so this stays a real regression check on the full generate pipeline
    rather than a restatement of canonicalize_plan_exercises's own logic."""
    from app.services.exercise_catalog import resolve_exercise

    canonical = []
    for day in program:
        exercises = []
        for ex in day["egzersizler"]:
            resolved = resolve_exercise(name=ex["isim"])
            exercises.append({
                **ex, "isim": resolved.canonical_name, "exercise_id": resolved.exercise_id,
            })
        canonical.append({**day, "egzersizler": exercises})
    return canonical


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
    assert body["program"] == _expect_canonicalized(PLAN_JSON["program"])
    assert body["overall_score"] == 8.3      # (8+8+9)/3
    assert body["score_label"] == "İyi"


def test_plan_zero_scores_are_schema_invalid(client, with_session, monkeypatch):
    plan = dict(PLAN_JSON, haftalik_ozet={"yogunluk_skoru": 0, "denge_skoru": 0,
                                          "uygunluk_skoru": 0})
    monkeypatch.setattr(training_bp, "_heavy_chat",
                        lambda **kwargs: json.dumps(plan, ensure_ascii=False))
    response = client.post("/training-plan", json={})
    assert response.status_code == 500
    assert response.get_json()["code"] == "TRAINING_PLAN_GENERATION_SCHEMA_INVALID"


def test_plan_invalid_llm_output_returns_500(client, with_session, monkeypatch):
    monkeypatch.setattr(training_bp, "_heavy_chat", lambda **kwargs: "tabii, işte planın:")
    assert client.post("/training-plan", json={}).status_code == 500


def test_plan_prompt_includes_cardio_preferences(client, with_session, monkeypatch):
    captured = {}
    lift = [{"isim": "Goblet Squat", "set": 3, "tekrar": "8-12",
             "dinlenme": "90 sn", "not": "RPE 7"}]
    run = [{"isim": "Run", "set": 1, "tekrar": "20 dk",
            "dinlenme": "—", "not": ""}]
    rest = {"tip": "dinlenme", "odak": "Aktif Toparlanma",
            "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": []}
    cardio_plan = {
        "program": [
            {"gun": "Pazartesi", "tip": "antrenman", "odak": "Full Body",
             "sure_dk": 45, "tahmini_kalori": 300, "egzersizler": lift},
            {"gun": "Salı", "tip": "antrenman", "odak": "Full Body",
             "sure_dk": 45, "tahmini_kalori": 300, "egzersizler": lift},
            {"gun": "Çarşamba", "tip": "antrenman", "odak": "Full Body",
             "sure_dk": 45, "tahmini_kalori": 300, "egzersizler": lift},
            {"gun": "Perşembe", "tip": "kardiyo", "odak": "Kondisyon",
             "sure_dk": 20, "tahmini_kalori": 180, "egzersizler": run},
            {"gun": "Cuma", "tip": "kardiyo", "odak": "Kondisyon",
             "sure_dk": 20, "tahmini_kalori": 180, "egzersizler": run},
            {"gun": "Cumartesi", **rest},
            {"gun": "Pazar", **rest},
        ],
        "haftalik_ozet": {"yogunluk_skoru": 7, "denge_skoru": 7, "uygunluk_skoru": 7},
    }

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return json.dumps(cardio_plan, ensure_ascii=False)
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

    first = _seven_day_program("Squat")
    second = _seven_day_program("Deadlift")
    client.post("/training-plan/save", json={"plan": first, "score": 7.0})
    client.post("/training-plan/save", json={"plan": second, "score": 8.0})
    plans = TrainingPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(plans) == 1
    assert json.loads(plans[0].plan_data) == second


def test_active_plan_roundtrip(client, auth_user):
    assert client.get("/training-plan/active").get_json() == {"exists": False}
    program = _seven_day_program()
    client.post("/training-plan/save", json={"plan": program, "score": 7.5})
    body = client.get("/training-plan/active").get_json()
    assert body["exists"] is True
    assert body["plan"] == program
    assert body["score"] == 7.5


# ---------------------------------------------------------------------------
# /workout/complete — Pump Check kapısı
# ---------------------------------------------------------------------------

@pytest.fixture
def workout_ready(client, auth_user):
    client.post("/training-plan/save", json={"plan": _seven_day_program(), "score": 7.0})
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
    assert check.visibility == "feed"
    assert check.shared_friend_ids == []

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
    # B3: tamamlanma sinyali PumpCheck'tir (idempotency guard'la aynı, M3) —
    # görev satırı değil. Görev tohumlanmamış ortamda quest-bazlı durum,
    # tamamlanan antrenmanı 'completed: False' gösteriyordu.
    # Sprint 7 PR1 (review Finding 4): /workout/status returns the additive `state`
    # snapshot alongside the preserved `completed` field. Assert the STRICT
    # contract — exact top-level shape (no field removed/added), closed enum
    # vocabularies, and completed↔snapshot consistency — not a loose field check.
    before = client.get("/workout/status").get_json()
    assert set(before) == {"completed", "state"}          # legacy field preserved, no extra
    assert before["completed"] is False
    assert_valid_state_contract(before["state"])          # keys + enum vocab + no `resume`
    assert before["state"]["completed_today"] is False    # snapshot mirrors legacy field

    db.session.add(PumpCheck(user_id=workout_ready.id, valid=True,
                             date_key=date.today().isoformat()))
    db.session.commit()

    after = client.get("/workout/status").get_json()
    assert set(after) == {"completed", "state"}
    assert after["completed"] is True
    assert_valid_state_contract(after["state"])
    assert after["state"]["completed_today"] is True
    assert after["state"]["execution_state"] == "completed"


def test_workout_status_true_even_without_seeded_quest(client, auth_user):
    # Görevsiz ortam (seed-quests koşmamış): PumpCheck varsa yine True dönmeli.
    # Strict contract preserved on both transitions (Finding 4).
    assert DailyQuest.query.filter_by(quest_type="workout_logged").first() is None
    before = client.get("/workout/status").get_json()
    assert set(before) == {"completed", "state"}
    assert before["completed"] is False
    assert_valid_state_contract(before["state"])
    db.session.add(PumpCheck(user_id=auth_user.id, valid=True,
                             date_key=date.today().isoformat()))
    db.session.commit()
    after = client.get("/workout/status").get_json()
    assert set(after) == {"completed", "state"}
    assert after["completed"] is True
    assert_valid_state_contract(after["state"])
    assert after["state"]["completed_today"] is True


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


def test_water_challenge_counts_days_not_posts(client, auth_user):
    """weekly_water 'bu hafta 5 GÜN' der — aynı gün bardak bardak 5 POST tek gün
    sayılmalı (triage 2026-07-19 #1). Huni yalnız günün 0→pozitif geçişinde ateşlenir."""
    from app.models import Challenge, UserChallengeProgress
    ch = Challenge(code="weekly_water", title="Su Kahramanı",
                   description="Bu hafta 5 gün su takibini gir", category="hydration",
                   metric="water_logged", target_value=5, xp_reward=75,
                   badge_code=None, challenge_type="global", period_type="weekly",
                   is_active=True)
    db.session.add(ch)
    db.session.commit()

    for n in (1, 2, 3, 4, 5):                       # bardak bardak, aynı gün
        assert client.post("/water", json={"count": n}).status_code == 200

    row = UserChallengeProgress.query.filter_by(
        user_id=auth_user.id, challenge_id=ch.id).first()
    assert row is not None
    assert row.progress == 1                        # 5 POST ≠ 5 gün
    assert row.completed_at is None


def _seed_weekly_water_challenge():
    from app.models import Challenge
    ch = Challenge(code="weekly_water", title="Su Kahramanı",
                   description="Bu hafta 5 gün su takibini gir", category="hydration",
                   metric="water_logged", target_value=5, xp_reward=75,
                   badge_code=None, challenge_type="global", period_type="weekly",
                   is_active=True)
    db.session.add(ch)
    db.session.commit()
    return ch


def test_water_challenge_survives_zero_toggle_replay(client, auth_user):
    """0↔pozitif toggle'ı huniyi YENİDEN silahlandırmamalı.

    Eski kapı mutable `prev_count`e bakıyordu: 5 → 0 → 5 dizisi geçişi tekrar
    üretip "5 GÜN" challenge'ını tek günde tamamlanabilir kılıyordu
    (triage 2026-08-07 #1 / 2026-08-14 #1). Kalıcı gün-başı işaret ilk pozitif
    kayıttan sonra sayıyı sıfırlamayı bir daha ateşlenme sebebi yapmaz.
    """
    from app.models import UserChallengeProgress
    ch = _seed_weekly_water_challenge()

    for _ in range(5):                              # aynı gün 5 kez toggle
        assert client.post("/water", json={"count": 5}).status_code == 200
        assert client.post("/water", json={"count": 0}).status_code == 200

    row = UserChallengeProgress.query.filter_by(
        user_id=auth_user.id, challenge_id=ch.id).first()
    assert row is not None
    assert row.progress == 1                        # 5 toggle ≠ 5 gün
    assert row.completed_at is None                 # challenge tek günde bitmez


def test_water_funnel_fires_on_a_genuinely_new_day(client, auth_user):
    """Gün-başı işaret huniyi SUSTURMAZ — yeni gün yeni satır, yeni ateşleme."""
    from app.models import UserChallengeProgress
    ch = _seed_weekly_water_challenge()

    assert client.post("/water", json={"count": 3}).status_code == 200
    # Dünün satırını taklit et: günün anahtarı serbest kalsın (uq_user_water_day
    # user+date_key üzerinde tek satır garanti eder).
    todays_row = WaterLog.query.filter_by(user_id=auth_user.id).one()
    todays_row.date_key = "2020-01-01"
    db.session.commit()

    assert client.post("/water", json={"count": 1}).status_code == 200

    row = UserChallengeProgress.query.filter_by(
        user_id=auth_user.id, challenge_id=ch.id).first()
    assert row.progress == 2                        # iki AYRI gün → iki ateşleme


def test_water_zero_post_never_fires_the_funnel(client, auth_user):
    """count=0 bir su kaydı DEĞİLDİR: işareti tüketmemeli."""
    from app.models import UserChallengeProgress
    ch = _seed_weekly_water_challenge()

    assert client.post("/water", json={"count": 0}).status_code == 200
    assert UserChallengeProgress.query.filter_by(
        user_id=auth_user.id, challenge_id=ch.id).first() is None

    assert client.post("/water", json={"count": 2}).status_code == 200
    row = UserChallengeProgress.query.filter_by(
        user_id=auth_user.id, challenge_id=ch.id).first()
    assert row.progress == 1                        # ilk POZİTİF kayıt ateşler


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

    assert body["program"] == _expect_canonicalized(PLAN_JSON["program"])
    assert body["classification"]["level"] in {"Beginner", "Intermediate", "Advanced"}
    assert "Final classified level" in captured["prompt"]
    assert "LLM sınıflandırma yapmayacak" in captured["prompt"]
    assert "ana kaldırış" in captured["prompt"]


# ---------------------------------------------------------------------------
# Persisted workout-session lifecycle routes (Sprint 7 PR3)
#
# The routes are gated by FITX_WORKOUT_SESSIONS_ENABLED (default OFF): OFF => every
# session route is INERT (404) and /workout/complete ignores a supplied session_id
# (byte-identical legacy path). ON => the full start->current->checkpoint->
# (resume|abandon|complete) lifecycle over the stable envelope. The flag is a
# rollout gate, NOT an auth gate -- @require_auth + server-side ownership always hold.
# ---------------------------------------------------------------------------
import secrets as _secrets

from app.models import (
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    WorkoutSession,
)
from app.timeutil import app_today
from app.services.training_generation.response_validator import WEEKDAYS


@pytest.fixture
def sessions_on(app):
    """Turn the persisted-session lifecycle flag ON for the duration of a test."""
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    yield app
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False


def _save_today_workout_plan(user_id):
    """Persist a plan whose today (Istanbul) weekday is an antrenman, so a freshly
    started session's relationship classifies as matching_current_plan (resumable)."""
    today_weekday = WEEKDAYS[app_today().weekday()]
    program = []
    for name in WEEKDAYS:
        if name == today_weekday:
            program.append({"gun": name, "tip": "antrenman",
                            "egzersizler": [{"isim": "Squat"}, {"isim": "Bench"}]})
        else:
            program.append({"gun": name, "tip": "dinlenme", "egzersizler": []})
    plan = TrainingPlan(user_id=user_id, score=5,
                        plan_data=json.dumps({"program": program, "haftalik_ozet": {}}))
    db.session.add(plan)
    db.session.commit()
    return plan


def _foreign_active_session(user_id):
    s = WorkoutSession(
        public_id=_secrets.token_urlsafe(16),
        user_id=user_id,
        status=WORKOUT_SESSION_ACTIVE,
        workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()],
        source="scheduled",
        version=1,
    )
    db.session.add(s)
    db.session.commit()
    return s


# -- Flag OFF: routes are inert -----------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/workout/session/start"),
    ("get", "/workout/session/current"),
    ("post", "/workout/session/anything/resume"),
    ("post", "/workout/session/anything/checkpoint"),
    ("post", "/workout/session/anything/abandon"),
])
def test_session_routes_are_404_when_flag_off(client, auth_user, method, path):
    resp = getattr(client, method)(path, json={})
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "not_found"
    # Nothing is ever persisted through the disabled surface.
    assert WorkoutSession.query.count() == 0


def test_complete_ignores_session_id_when_flag_off(client, with_session, monkeypatch):
    client.post("/training-plan/save", json={"plan": _seven_day_program(), "score": 7.0})
    monkeypatch.setattr(training_bp, "validate_pump_check_image",
                        lambda *a, **k: (b"jpeg", "image/jpeg", None))
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a, **k: {"valid": True, "fallback": False})
    resp = client.post("/workout/complete",
                       json={"image": "x", "location_type": "salon",
                             "session_id": "does-not-exist"})
    # Legacy path: the bogus session_id is never read; completion succeeds and no
    # session row is fabricated.
    assert resp.status_code == 200
    assert "session_completed" not in resp.get_json()
    assert WorkoutSession.query.count() == 0


# -- Flag ON: full lifecycle --------------------------------------------------

def test_start_current_checkpoint_lifecycle(client, auth_user, sessions_on):
    started = client.post("/workout/session/start", json={})
    assert started.status_code == 201
    body = started.get_json()
    assert body["outcome"] == "created"
    public_id = body["session"]["public_id"]
    assert public_id

    current = client.get("/workout/session/current")
    assert current.status_code == 200
    assert current.get_json()["session"]["public_id"] == public_id

    checkpoint = client.post(f"/workout/session/{public_id}/checkpoint", json={})
    assert checkpoint.status_code == 200
    assert checkpoint.get_json()["outcome"] == "checkpointed"


def test_start_twice_is_existing_active(client, auth_user, sessions_on):
    first = client.post("/workout/session/start", json={})
    second = client.post("/workout/session/start", json={})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["outcome"] == "existing_active"
    # The idempotent replay returns the SAME session -- never a second ACTIVE row.
    assert first.get_json()["session"]["public_id"] == second.get_json()["session"]["public_id"]
    assert WorkoutSession.query.filter_by(
        user_id=auth_user.id, status=WORKOUT_SESSION_ACTIVE).count() == 1


def test_resume_matching_plan_returns_resumed(client, auth_user, sessions_on):
    _save_today_workout_plan(auth_user.id)
    public_id = client.post("/workout/session/start", json={}).get_json()["session"]["public_id"]
    resumed = client.post(f"/workout/session/{public_id}/resume", json={})
    assert resumed.status_code == 200
    assert resumed.get_json()["outcome"] == "resumed"


def test_abandon_is_idempotent_over_the_route(client, auth_user, sessions_on):
    public_id = client.post("/workout/session/start", json={}).get_json()["session"]["public_id"]
    first = client.post(f"/workout/session/{public_id}/abandon",
                        json={"reason": "changed my mind"})
    assert first.status_code == 200
    assert first.get_json()["outcome"] == "abandoned"
    # A retried abandon is a deterministic no-op, never a false conflict.
    second = client.post(f"/workout/session/{public_id}/abandon", json={})
    assert second.status_code == 200
    assert second.get_json()["outcome"] == "already_abandoned"
    row = WorkoutSession.query.filter_by(user_id=auth_user.id).one()
    assert row.status == WORKOUT_SESSION_ABANDONED


@pytest.mark.parametrize("verb", ["resume", "checkpoint", "abandon"])
def test_cannot_touch_another_users_session(client, auth_user, make_user, sessions_on, verb):
    other = make_user("other_owner")
    foreign = _foreign_active_session(other.id)
    resp = client.post(f"/workout/session/{foreign.public_id}/{verb}", json={})
    # Ownership is re-enforced server-side: the caller cannot even observe another
    # user's session -- it is indistinguishable from a non-existent one.
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "not_found"
    # The victim's session is untouched.
    assert db.session.get(WorkoutSession, foreign.id).status == WORKOUT_SESSION_ACTIVE


def test_complete_with_session_terminalizes_it(client, auth_user, sessions_on, monkeypatch):
    _save_today_workout_plan(auth_user.id)
    public_id = client.post("/workout/session/start", json={}).get_json()["session"]["public_id"]
    monkeypatch.setattr(training_bp, "validate_pump_check_image",
                        lambda *a, **k: (b"jpeg", "image/jpeg", None))
    monkeypatch.setattr(training_bp, "validate_pump_check",
                        lambda *a, **k: {"valid": True, "fallback": False})

    resp = client.post("/workout/complete",
                       json={"image": "x", "location_type": "salon",
                             "session_id": public_id})
    assert resp.status_code == 200
    assert resp.get_json()["session_completed"] is True
    # The linked session was terminalized COMPLETED atomically with the completion.
    row = WorkoutSession.query.filter_by(user_id=auth_user.id).one()
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.completed_at is not None
