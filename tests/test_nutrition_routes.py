"""Route tests for the nutrition blueprint (app/blueprints/nutrition/).

Beslenme günlüğü (porsiyon/gram matematiği, upsert, loglama), plan kaydetme,
plandan hızlı ekleme, AI'lı öğün loglama (fitness sözlüğü normalizasyonu ve
override yolu dahil) ve plan üretimi — LLM mock'lu.

Not: blueprint artık paket; AI yardımcıları alt-modüllerde yaşar
(_openai_chat → meallog, _heavy_chat → plan), monkeypatch'ler oraya hedeflenir.

    python -m pytest tests/test_nutrition_routes.py -v
"""
import json

import pytest

from app.blueprints import nutrition as nutrition_bp
from app.blueprints.nutrition import meallog as nutrition_meallog
from app.blueprints.nutrition import plan as nutrition_plan
from app.extensions import db
from app.models import CustomMeal, CustomMealItem, MealLog, NutritionPlan, UserSession


# ---------------------------------------------------------------------------
# Plan kaydet / aktif plan / hızlı ekleme
# ---------------------------------------------------------------------------

def test_save_plan_replaces_previous(client, auth_user):
    assert client.post("/nutrition-plan/save", json={}).status_code == 400
    client.post("/nutrition-plan/save", json={"plan": {"v": 1}, "score": 6.0})
    client.post("/nutrition-plan/save", json={"plan": {"v": 2}, "score": 7.0})
    plans = NutritionPlan.query.filter_by(user_id=auth_user.id).all()
    assert len(plans) == 1
    assert json.loads(plans[0].plan_data) == {"v": 2}


def test_active_plan_roundtrip(client, auth_user):
    assert client.get("/nutrition-plan/active").get_json() == {"exists": False}
    client.post("/nutrition-plan/save", json={"plan": {"v": 1}, "score": 8.0})
    body = client.get("/nutrition-plan/active").get_json()
    assert body["exists"] is True and body["plan"] == {"v": 1}


def test_quick_add_meal(client, auth_user):
    assert client.post("/api/quick-add-meal",
                       json={"meal_key": "brunch"}).status_code == 400   # geçersiz anahtar
    assert client.post("/api/quick-add-meal",
                       json={"meal_key": "ogle"}).status_code == 404     # plan yok

    plan = {"ogle": {"yemekler": ["Tavuk - 150g", "Pirinç - 100g"],
                     "kalori": 380, "protein": 48, "karb": 28, "yag": 5}}
    client.post("/nutrition-plan/save", json={"plan": plan, "score": 8.0})

    assert client.post("/api/quick-add-meal",
                       json={"meal_key": "aksam"}).status_code == 404    # planda tanımsız

    body = client.post("/api/quick-add-meal", json={"meal_key": "ogle"}).get_json()
    assert body["nutrients"]["kalori"] == 380.0
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.source == "ai_plan"
    assert entry.ogun == "Öğle"


# ---------------------------------------------------------------------------
# Günlük (diary) — öğün oluşturma + besin matematiği
# ---------------------------------------------------------------------------

def test_diary_create_meal_upserts(client, auth_user):
    assert client.post("/api/diary/meal",
                       json={"meal_name": "Brunch"}).status_code == 400
    first = client.post("/api/diary/meal", json={"meal_name": "Kahvaltı"}).get_json()
    assert first["exists"] is False
    again = client.post("/api/diary/meal", json={"meal_name": "Kahvaltı"}).get_json()
    assert again == {"meal_id": first["meal_id"], "exists": True}


def test_diary_create_meal_race_returns_existing(client, auth_user, monkeypatch):
    """İki eşzamanlı POST aynı (user, meal_name, date_key) için existence-check'i
    aşıp ikisi de INSERT denerse, ikincisi uq_custom_meal_day'i ihlal eder. Bu
    IntegrityError 500 yerine yakalanmalı: rollback + re-query → mevcut satır."""
    from app.blueprints.nutrition import diary as diary_mod

    first = client.post("/api/diary/meal", json={"meal_name": "Akşam"}).get_json()
    assert first["exists"] is False

    # Yarışı simüle et: bir sonraki istekte ilk existence-check None görsün (sanki
    # eşzamanlı istek henüz commit etmedi); INSERT ise uq ihlaliyle patlasın.
    real_query = diary_mod.CustomMeal.query
    state = {"first_check": True}

    class _RaceQuery:
        def filter_by(self, **kw):
            real = real_query.filter_by(**kw)
            if state["first_check"]:
                state["first_check"] = False
                class _Miss:
                    def first(self_inner):
                        return None
                return _Miss()
            return real

    monkeypatch.setattr(diary_mod.CustomMeal, "query", _RaceQuery())

    resp = client.post("/api/diary/meal", json={"meal_name": "Akşam"})
    assert resp.status_code == 200
    assert resp.get_json() == {"meal_id": first["meal_id"], "exists": True}


@pytest.fixture
def meal_id(client, auth_user):
    return client.post("/api/diary/meal", json={"meal_name": "Öğle"}).get_json()["meal_id"]


def test_diary_add_item_requires_name(client, meal_id):
    response = client.post(f"/api/diary/meal/{meal_id}/item", json={})
    assert response.status_code == 400


def test_diary_add_item_grams_based_scaling(client, meal_id):
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 200,
        "per_100g": {"calories": 130, "protein": 2.7, "carbs": 28, "fat": 0.3},
    }).get_json()
    assert body["calories"] == 260.0
    assert body["carbs"] == 56.0


def test_diary_add_item_serving_based_math(client, meal_id):
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "yumurta", "serving_id": "s1", "serving_quantity": 3,
        "serving_description": "1 adet", "metric_serving_amount": 60,
        "serving_calories": 90, "serving_protein": 7,
        "serving_carbs": 0.6, "serving_fat": 6.3,
    }).get_json()
    assert body["calories"] == 270.0       # 90 * 3
    item = db.session.get(CustomMealItem, body["item_id"])
    assert item.grams == 180.0             # 60g * 3
    assert item.per_100g_calories == 150.0  # 90/60*100


def test_diary_add_item_garbage_per100g_defaults_zero(client, meal_id):
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "bilinmez", "grams": 100, "per_100g": "bozuk"}).get_json()
    assert body["calories"] == 0.0


def test_diary_update_item_three_branches(client, meal_id):
    item_id = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 100,
        "per_100g": {"calories": 130, "protein": 2.7, "carbs": 28, "fat": 0.3},
    }).get_json()["item_id"]

    # 1) gram bazlı güncelleme → per-100g'den yeniden ölçekle
    body = client.patch(f"/api/diary/item/{item_id}", json={"grams": 50}).get_json()
    assert body["calories"] == 65.0

    # 2) porsiyona geçiş (serving_id)
    body = client.patch(f"/api/diary/item/{item_id}", json={
        "serving_id": "s9", "serving_quantity": 2, "metric_serving_amount": 150,
        "serving_calories": 195, "serving_protein": 4, "serving_carbs": 42,
        "serving_fat": 0.5, "serving_description": "1 tabak"}).get_json()
    assert body["calories"] == 390.0
    assert body["grams"] == 300.0

    # 3) yalnız adet değişimi → oranla
    body = client.patch(f"/api/diary/item/{item_id}",
                        json={"serving_quantity": 1}).get_json()
    assert body["calories"] == 195.0
    assert body["grams"] == 150.0


def test_diary_delete_item(client, meal_id):
    item_id = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "x", "grams": 100,
        "per_100g": {"calories": 100}}).get_json()["item_id"]
    assert client.delete(f"/api/diary/item/{item_id}").get_json() == {"deleted": True}
    assert db.session.get(CustomMealItem, item_id) is None


def test_diary_log_meal_totals_labels_and_lock(client, auth_user, meal_id):
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 400  # boş öğün

    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 200,
        "per_100g": {"calories": 130, "protein": 2.7, "carbs": 28, "fat": 0.3}})
    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "yumurta", "serving_id": "s1", "serving_quantity": 2,
        "serving_description": "1 adet", "metric_serving_amount": 60,
        "serving_calories": 90, "serving_protein": 7, "serving_carbs": 0.6,
        "serving_fat": 6.3})

    body = client.post(f"/api/diary/meal/{meal_id}/log").get_json()
    assert body["nutrients"]["kalori"] == 260.0 + 180.0

    entry = MealLog.query.filter_by(user_id=auth_user.id, source="diary").one()
    assert "pirinç (200g)" in entry.yemekler
    assert "yumurta (2x 1 adet)" in entry.yemekler

    # Kilitlendi: tekrar log/ekleme/düzenleme reddedilir.
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 400
    assert client.post(f"/api/diary/meal/{meal_id}/item",
                       json={"food_name": "y"}).status_code == 400


def test_diary_today_aggregates(client, auth_user, meal_id):
    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 100, "per_100g": {"calories": 130}})
    body = client.get("/api/diary/today").get_json()
    assert len(body["meals"]) == 1
    assert body["meals"][0]["totals"]["calories"] == 130.0
    assert body["totals"]["calories"] == 130.0


# ---------------------------------------------------------------------------
# /meal-log — AI'lı serbest metin loglama
# ---------------------------------------------------------------------------

def test_meal_log_requires_fields(client, auth_user):
    assert client.post("/meal-log", json={"ogun": "Kahvaltı"}).status_code == 400


def test_meal_log_rejects_bad_photo(client, auth_user):
    response = client.post("/meal-log", json={
        "ogun": "Kahvaltı", "yemekler": "muz", "image": "https://evil.example/a.jpg"})
    assert response.status_code == 400


def test_meal_log_override_macros_skips_ai(client, auth_user, monkeypatch):
    def boom(**kwargs):
        raise AssertionError("override varken AI çağrılmamalı")
    monkeypatch.setattr(nutrition_meallog, "_openai_chat", boom)

    body = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "tavuk",
        "override_macros": {"kalori": "495", "protein": 62, "karb": 0, "yag": 10.5},
    }).get_json()
    assert body["nutrients"] == {"kalori": 495.0, "protein": 62.0, "karb": 0.0, "yag": 10.5}


def test_meal_log_ai_path_with_fitness_normalization(client, auth_user, monkeypatch):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return '```json\n{"kalori": 240, "protein": 48, "karb": 6, "yag": 3}\n```'
    monkeypatch.setattr(nutrition_meallog, "_openai_chat", fake_chat)

    body = client.post("/meal-log", json={
        "ogun": "Ara Öğün", "yemekler": "2 ölçek whey ve muz"}).get_json()
    assert body["nutrients"]["kalori"] == 240.0
    # Fitness sözlüğü: '2 ölçek whey' prompt'a gramajıyla normalize edilir (2*30g).
    assert "2 ölçek whey protein tozu (60g)" in captured["prompt"]

    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.yemekler == "2 ölçek whey ve muz"  # DB'ye orijinal metin yazılır


def test_meal_log_ai_unparseable_returns_error_no_save(client, auth_user, monkeypatch):
    # AI yanıtı parse edilemezse kanonik MealLog defterine sıfır-makro satırı
    # YAZILMAMALI (TRIAGE_FIXES #1) — hata döner, kayıt eklenmez.
    monkeypatch.setattr(nutrition_meallog, "_openai_chat", lambda **kw: "hesaplayamadım")
    resp = client.post("/meal-log", json={"ogun": "Öğle", "yemekler": "şey"})
    assert resp.status_code == 502
    assert "error" in resp.get_json()
    assert MealLog.query.count() == 0  # bozuk satır defter'e yazılmaz


def test_meal_log_non_numeric_ai_values_zeroed(client, auth_user, monkeypatch):
    monkeypatch.setattr(nutrition_meallog, "_openai_chat",
                        lambda **kw: '{"kalori": "çok", "protein": 30, "karb": 1, "yag": 2}')
    body = client.post("/meal-log", json={"ogun": "Öğle", "yemekler": "tavuk"}).get_json()
    assert body["nutrients"]["kalori"] == 0
    assert body["nutrients"]["protein"] == 30.0


# ---------------------------------------------------------------------------
# Bugün / geçmiş / değerlendirme
# ---------------------------------------------------------------------------

def _log_meal(client, ogun, kalori):
    client.post("/meal-log", json={"ogun": ogun, "yemekler": "x",
                                   "override_macros": {"kalori": kalori, "protein": 10,
                                                       "karb": 10, "yag": 5}})


def test_meal_log_today_totals(client, auth_user):
    _log_meal(client, "Kahvaltı", 400)
    _log_meal(client, "Öğle", 600)
    body = client.get("/meal-log/today").get_json()
    assert body["totals"]["kalori"] == 1000.0
    assert [m["ogun"] for m in body["meals"]] == ["Kahvaltı", "Öğle"]


def test_meal_history_groups_by_day(client, auth_user):
    _log_meal(client, "Kahvaltı", 400)
    _log_meal(client, "Öğle", 500)
    days = client.get("/meal-log/history").get_json()
    assert len(days) == 1
    assert days[0]["totals"]["kalori"] == 900.0
    assert len(days[0]["meals"]) == 2


def test_review_requires_meals(client, auth_user):
    assert client.post("/meal-log/review", json={}).status_code == 400


def test_review_returns_ai_text_and_totals(client, auth_user, monkeypatch):
    _log_meal(client, "Kahvaltı", 800)
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000,
                               goal="kilo verme"))
    db.session.commit()
    monkeypatch.setattr(nutrition_meallog, "_openai_chat", lambda **kw: "Gayet dengeli.")
    body = client.post("/meal-log/review", json={}).get_json()
    assert body == {"review": "Gayet dengeli.", "total_calories": 800, "target": 2000}


def test_review_ai_failure_falls_back(client, auth_user, monkeypatch):
    _log_meal(client, "Kahvaltı", 800)

    def boom(**kwargs):
        raise RuntimeError("openai down")
    monkeypatch.setattr(nutrition_meallog, "_openai_chat", boom)
    body = client.post("/meal-log/review", json={}).get_json()
    assert "tekrar dene" in body["review"]


# ---------------------------------------------------------------------------
# /nutrition-plan (AI üretimi)
# ---------------------------------------------------------------------------

PLAN_REQUEST = {"proteins": ["Tavuk Göğsü"], "carbs": ["Pirinç"], "fats": ["Zeytinyağı"]}
PLAN_RESPONSE = {"planlar": [{"isim": "Plan A", "toplam_kalori": 2000}]}


def test_nutrition_plan_requires_session_and_selection(client, auth_user):
    assert client.post("/nutrition-plan", json=PLAN_REQUEST).status_code == 400  # oturum yok

    db.session.add(UserSession(user_id=auth_user.id, target_calories=2300,
                               goal="kas kazanma"))
    db.session.commit()
    response = client.post("/nutrition-plan", json={"proteins": [], "carbs": [], "fats": []})
    assert response.status_code == 400                                            # seçim yok


def test_nutrition_plan_scores_selection_and_returns_plans(client, auth_user, monkeypatch):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2300,
                               goal="kas kazanma"))
    db.session.commit()
    monkeypatch.setattr(nutrition_plan, "_heavy_chat",
                        lambda **kw: json.dumps(PLAN_RESPONSE, ensure_ascii=False))

    body = client.post("/nutrition-plan",
                       json={**PLAN_REQUEST, "custom_foods": ["kefir"]}).get_json()
    assert body["planlar"] == PLAN_RESPONSE["planlar"]
    assert body["target_calories"] == 2300
    # Tavuk(8,9,10) + Pirinç(6,8,10) + Zeytinyağı(9,9,10) → 8.8 → "İyi"
    assert body["overall_score"] == 8.8
    assert body["score_label"] == "İyi"


def test_nutrition_plan_bad_llm_json_returns_500(client, auth_user, monkeypatch):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000, goal="x"))
    db.session.commit()
    monkeypatch.setattr(nutrition_plan, "_heavy_chat", lambda **kw: "plan yapamadım")
    assert client.post("/nutrition-plan", json=PLAN_REQUEST).status_code == 500


def test_nutrition_page_renders(client, auth_user):
    assert client.get("/nutrition").status_code == 200
