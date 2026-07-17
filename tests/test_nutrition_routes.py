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
from app.blueprints.nutrition.diary import _claim_diary_meal
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


def test_quick_add_meal_handles_malformed_plan(client, auth_user):
    # A4: LLM planı sayısal-olmayan makro / liste-olmayan yemekler içerebilir →
    # 500 yerine güvenli değerlerle eklenmeli.
    plan = {"ogle": {"yemekler": "Tek string yemek", "kalori": "400 kcal",
                     "protein": None, "karb": 30, "yag": 5}}
    client.post("/nutrition-plan/save", json={"plan": plan, "score": 8.0})
    resp = client.post("/api/quick-add-meal", json={"meal_key": "ogle"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["nutrients"]["kalori"] == 0      # "400 kcal" → güvenli 0
    assert body["nutrients"]["karb"] == 30.0
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.yemekler == "Tek string yemek"  # str → tek elemanlı listeye indirildi


def test_quick_add_meal_empty_meal_dict_rejected(client, auth_user):
    # A4: boş öğün ({}) 0-makro satır yazmamalı — eski `if not meal` davranışı korunur.
    client.post("/nutrition-plan/save", json={"plan": {"ogle": {}}, "score": 8.0})
    assert client.post("/api/quick-add-meal", json={"meal_key": "ogle"}).status_code == 404
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 0


def test_quick_add_meal_floors_negative_macros(client, auth_user):
    plan = {"ogle": {"yemekler": ["Hatalı plan"], "kalori": -50,
                     "protein": -5, "karb": -2, "yag": -1}}
    client.post("/nutrition-plan/save", json={"plan": plan, "score": 8.0})
    response = client.post("/api/quick-add-meal", json={"meal_key": "ogle"})
    assert response.status_code == 200
    assert response.get_json()["nutrients"] == {
        "kalori": 0, "protein": 0, "karb": 0, "yag": 0,
    }


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


def test_claim_diary_meal_only_succeeds_once(auth_user, meal_id):
    assert _claim_diary_meal(meal_id, auth_user.id) == 1
    assert _claim_diary_meal(meal_id, auth_user.id) == 0


def test_diary_add_item_requires_name(client, meal_id):
    response = client.post(f"/api/diary/meal/{meal_id}/item", json={})
    assert response.status_code == 400


def test_diary_add_item_negative_metric_no_negative_macros(client, meal_id):
    # B8: negatif metric_serving_amount negatif gram/per-100g üretip MealLog'a
    # sızıyordu. Artık ≥0'a kısılır — hiçbir makro/gram negatif olamaz.
    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Hile", "serving_id": "s1", "serving_quantity": 1,
        "serving_calories": 100, "serving_protein": 5,
        "metric_serving_amount": -50}).get_json()
    item = CustomMealItem.query.filter_by(custom_meal_id=meal_id).one()
    assert item.grams >= 0
    assert (item.per_100g_calories or 0) >= 0
    assert (item.per_100g_protein or 0) >= 0


def test_diary_add_item_negative_serving_macros_floored(client, meal_id):
    # B6: negatif per-serving makrolar (serving_calories/protein/...) eskiden
    # kırpılmadan CustomMealItem'a ve oradan MealLog'a sızıp günlük toplamları
    # aşağı çekiyordu. Artık tüm makrolar ≥0'a taban yapılır.
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Hile", "serving_id": "s1", "serving_quantity": 2,
        "metric_serving_amount": 100,
        "serving_calories": -500, "serving_protein": -30,
        "serving_carbs": -20, "serving_fat": -10,
    }).get_json()
    assert body["calories"] >= 0
    assert body["protein"] >= 0
    assert body["carbs"] >= 0
    assert body["fat"] >= 0
    item = db.session.get(CustomMealItem, body["item_id"])
    assert item.calories >= 0 and item.grams >= 0


def test_diary_add_item_negative_grams_branch_floored(client, meal_id):
    # B6: gram bazlı dalda negatif grams / negatif per_100g de MealLog'u
    # bozabiliyordu. Makrolar ≥0'a kısılmalı.
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Hile2", "grams": -200,
        "per_100g": {"calories": 130, "protein": 2.7, "carbs": 28, "fat": 0.3},
    }).get_json()
    assert body["calories"] >= 0
    assert body["protein"] >= 0
    item = db.session.get(CustomMealItem, body["item_id"])
    assert item.grams >= 0


def test_diary_add_item_clamps_absurd_macros(client, meal_id):
    # H1: diary hattı eskiden YALNIZCA negatifleri 0'a çekiyordu; üst fiziksel-
    # tavan yoktu, istemci serving_calories: 90000 değerini doğrudan CustomMealItem'a
    # ve oradan kanonik MealLog'a sızdırabiliyordu. Artık clamp_serving_macros ile
    # diğer tüm ingest hatlarıyla aynı fiziksel sınırlara kısılır. Makrolar sıfır
    # olduğundan Atwater düzeltmesi desteklenmeyen kaloriyi de sıfıra indirir.
    body = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "Hile", "serving_id": "s1", "serving_quantity": 1,
        "metric_serving_amount": 100,
        "serving_calories": 90000, "serving_protein": 0,
        "serving_carbs": 0, "serving_fat": 0,
    }).get_json()
    assert body["calories"] == 0
    item = db.session.get(CustomMealItem, body["item_id"])
    assert item.calories == 0


def test_diary_update_item_clamps_absurd_macros(client, meal_id):
    # H1: güncelleme yolu da kısar — makul bir öğeyi sonradan 90000 kcal'lik bir
    # serving'e PATCH etmek tavanı aşamaz.
    item_id = client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "yumurta", "grams": 100,
        "per_100g": {"calories": 150, "protein": 12, "carbs": 1, "fat": 10},
    }).get_json()["item_id"]
    body = client.patch(f"/api/diary/item/{item_id}", json={
        "serving_id": "s9", "serving_quantity": 1, "metric_serving_amount": 100,
        "serving_calories": 90000, "serving_protein": 0,
        "serving_carbs": 0, "serving_fat": 0,
    }).get_json()
    assert body["calories"] == 0


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
    assert MealLog.query.filter_by(user_id=auth_user.id, source="diary").count() == 1
    assert client.post(f"/api/diary/meal/{meal_id}/item",
                       json={"food_name": "y"}).status_code == 400


def test_diary_today_aggregates(client, auth_user, meal_id):
    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 100,
        "per_100g": {"calories": 130, "protein": 2.5, "carbs": 28, "fat": 0.6}})
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


def test_today_meal_includes_created_at(client, auth_user):
    # Phase 4: öğün kartı "saat" alanı için /meal-log/today her öğüne created_at
    # (ISO) döndürmeli. Additive alan — mevcut anahtarlar korunur.
    client.post("/meal-log", json={
        "ogun": "Kahvaltı", "yemekler": "yumurta",
        "override_macros": {"kalori": 180, "protein": 14, "karb": 1, "yag": 12},
    })
    data = client.get("/meal-log/today").get_json()
    assert data["meals"], "en az bir öğün olmalı"
    first = data["meals"][0]
    assert "created_at" in first
    assert first["created_at"]  # None/boş değil
    # Mevcut anahtarlar hâlâ mevcut (regresyon değil)
    for k in ("ogun", "yemekler", "kalori", "protein", "karb", "yag", "photo_url"):
        assert k in first


def test_meal_log_override_macros_clamped_to_physical_bounds(client, auth_user, monkeypatch):
    # C1: request-kontrollü override değerleri kanonik MealLog'a YAZILMADAN ÖNCE
    # fiziksel-sağlık kapısından (clamp_serving_macros) geçmeli — DB CHECK yalnızca
    # >100000 kcal kaba taşmayı yakalar, "99999 kcal" çöpünü değil.
    monkeypatch.setattr(nutrition_meallog, "_openai_chat",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("AI çağrılmamalı")))
    body = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "tavuk",
        "override_macros": {"kalori": 99999, "protein": 9999, "karb": 9999, "yag": 9999},
    }).get_json()
    n = body["nutrients"]
    assert n["kalori"] <= 3000 and n["protein"] <= 300 and n["karb"] <= 300 and n["yag"] <= 150
    # Kalıcı satır da kısılmış olmalı (defter bozulmadı).
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert entry.kalori <= 3000 and entry.yag <= 150


def test_meal_log_override_floors_negative_macros(client, auth_user):
    response = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "hatalı giriş",
        "override_macros": {"kalori": -10, "protein": -2, "karb": -3, "yag": -4},
    })
    assert response.status_code == 200
    assert response.get_json()["nutrients"] == {
        "kalori": 0, "protein": 0, "karb": 0, "yag": 0,
    }


def test_meal_log_override_macros_awards_meal_logged_quest(client, auth_user, monkeypatch):
    # C5: override yolu AI-hesaplı yolla AYNI 'meal_logged' görevini vermeli —
    # elle makro giren kullanıcı günlük görev/XP'yi sessizce kaçırıyordu.
    from app.models import DailyQuest, UserQuestProgress
    db.session.add(DailyQuest(title="Öğün Kaydet", description="Bugün bir öğün kaydet",
                              points_reward=20, quest_type="meal_logged"))
    db.session.commit()
    monkeypatch.setattr(nutrition_meallog, "_openai_chat",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("AI çağrılmamalı")))

    body = client.post("/meal-log", json={
        "ogun": "Akşam", "yemekler": "tavuk",
        "override_macros": {"kalori": 495, "protein": 62, "karb": 0, "yag": 10.5},
    }).get_json()

    assert body["quest_awarded"]["xp"] == 20
    assert UserQuestProgress.query.filter_by(user_id=auth_user.id).count() == 1
    db.session.expire_all()
    assert db.session.get(type(auth_user), auth_user.id).rank_points == 20


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

def test_ai_meal_total_sanitized_before_persistence(client, auth_user, monkeypatch):
    monkeypatch.setattr(
        nutrition_meallog,
        "_openai_chat",
        lambda **kw: '{"kalori": 20000, "protein": -2, "karb": 2000, "yag": 500}',
    )

    response = client.post(
        "/meal-log", json={"ogun": "Ogle", "yemekler": "tavuk"})

    assert response.status_code == 200
    expected = {
        "kalori": 10000.0,
        "protein": 0,
        "karb": 1000.0,
        "yag": 250.0,
    }
    assert response.get_json()["nutrients"] == expected
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert {
        "kalori": entry.kalori,
        "protein": entry.protein,
        "karb": entry.karb,
        "yag": entry.yag,
    } == expected


def test_ai_meal_total_oversized_numeric_zeroed_before_persistence(
        client, auth_user, monkeypatch):
    monkeypatch.setattr(
        nutrition_meallog,
        "_openai_chat",
        lambda **kw: json.dumps({
            "kalori": 10 ** 400,
            "protein": 30,
            "karb": 10,
            "yag": 5,
        }),
    )

    response = client.post(
        "/meal-log", json={"ogun": "Ogle", "yemekler": "tavuk"})

    assert response.status_code == 200
    expected = {"kalori": 0, "protein": 30.0, "karb": 10.0, "yag": 5.0}
    assert response.get_json()["nutrients"] == expected
    entry = MealLog.query.filter_by(user_id=auth_user.id).one()
    assert {
        "kalori": entry.kalori,
        "protein": entry.protein,
        "karb": entry.karb,
        "yag": entry.yag,
    } == expected


def test_ai_meal_total_normalization_logging_omits_meal_content(
        client, auth_user, monkeypatch, caplog):
    captured = {}

    def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return '{"kalori": 240, "protein": 48, "karb": 6, "yag": 3}'

    monkeypatch.setattr(nutrition_meallog, "_openai_chat", fake_chat)
    sensitive_meal = "2 scoop whey private-diet-token-7f1a"
    normalized_fragment = "2 \u00f6l\u00e7ek whey protein tozu (60g)"
    caplog.clear()

    with caplog.at_level("INFO"):
        response = client.post(
            "/meal-log",
            json={"ogun": "Ara Ogun", "yemekler": sensitive_meal},
        )

    assert response.status_code == 200
    assert normalized_fragment in captured["prompt"]
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "[MEAL] Fitness shorthand normalized" in messages
    assert sensitive_meal not in messages
    assert normalized_fragment not in messages
    assert "private-diet-token-7f1a" not in messages

# ---------------------------------------------------------------------------
# Bugün / geçmiş / değerlendirme
# ---------------------------------------------------------------------------

def _log_meal(client, ogun, kalori):
    client.post("/meal-log", json={"ogun": ogun, "yemekler": "x",
                                   "override_macros": {"kalori": kalori, "protein": 0,
                                                       "karb": kalori / 4, "yag": 0}})


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


def test_nutrition_plan_rejects_session_without_target_calories(client, auth_user, monkeypatch):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=None,
                               goal="kas kazanma"))
    db.session.commit()
    monkeypatch.setattr(nutrition_plan, "_heavy_chat",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("AI çağrılmamalı")))

    response = client.post("/nutrition-plan", json=PLAN_REQUEST)

    assert response.status_code == 400
    assert response.get_json()["error"]


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


# ---------------------------------------------------------------------------
# Meal-write idempotency
# ---------------------------------------------------------------------------

_IDEMPOTENCY_HEADERS = {
    "Idempotency-Key": "018f47d2-a2c7-7f52-a5b0-123456789abc",
}


def test_meal_log_idempotency_replays_before_second_ai_call(client, auth_user, monkeypatch):
    calls = {"count": 0}

    def fake_chat(**kwargs):
        calls["count"] += 1
        return '{"kalori": 240, "protein": 48, "karb": 6, "yag": 3}'

    monkeypatch.setattr(nutrition_meallog, "_openai_chat", fake_chat)
    payload = {"ogun": "Ogle", "yemekler": "tavuk"}

    first = client.post("/meal-log", json=payload, headers=_IDEMPOTENCY_HEADERS)
    second = client.post("/meal-log", json=payload, headers=_IDEMPOTENCY_HEADERS)

    assert first.status_code == second.status_code == 200
    assert second.get_json()["nutrients"] == first.get_json()["nutrients"]
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert calls["count"] == 1


def test_quick_add_meal_idempotency_writes_one_row(client, auth_user):
    plan = {"ogle": {"yemekler": ["Tavuk"], "kalori": 380,
                     "protein": 48, "karb": 28, "yag": 5}}
    client.post("/nutrition-plan/save", json={"plan": plan, "score": 8.0})

    first = client.post("/api/quick-add-meal", json={"meal_key": "ogle"},
                        headers=_IDEMPOTENCY_HEADERS)
    second = client.post("/api/quick-add-meal", json={"meal_key": "ogle"},
                         headers=_IDEMPOTENCY_HEADERS)

    assert first.status_code == second.status_code == 200
    assert second.get_json()["nutrients"] == first.get_json()["nutrients"]
    assert MealLog.query.filter_by(user_id=auth_user.id, source="ai_plan").count() == 1


def test_meal_log_idempotency_is_scoped_to_authenticated_user(
        client, auth_user, make_user, login):
    payload = {
        "ogun": "Aksam", "yemekler": "tavuk",
        "override_macros": {"kalori": 495, "protein": 62, "karb": 0, "yag": 10.5},
    }
    assert client.post("/meal-log", json=payload,
                       headers=_IDEMPOTENCY_HEADERS).status_code == 200

    other = make_user("second-user")
    assert login("second-user").status_code == 200
    assert client.post("/meal-log", json=payload,
                       headers=_IDEMPOTENCY_HEADERS).status_code == 200

    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert MealLog.query.filter_by(user_id=other.id).count() == 1

def test_nutrition_page_renders(client, auth_user):
    assert client.get("/nutrition").status_code == 200


def test_meal_idempotency_integrity_race_returns_existing_winner(
        app, auth_user, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from app.services import meal_idempotency

    key = "018f47d2-a2c7-7f52-a5b0-123456789abc"
    winner = MealLog(
        user_id=auth_user.id, ogun="Ogle", yemekler="winner",
        kalori=1, protein=1, karb=1, yag=1, tarih="2026-07-17",
        idempotency_key=key,
    )
    db.session.add(winner)
    db.session.commit()

    candidate = MealLog(
        user_id=auth_user.id, ogun="Ogle", yemekler="candidate",
        kalori=2, protein=2, karb=2, yag=2, tarih="2026-07-17",
    )

    def lose_race():
        raise IntegrityError("insert", {}, Exception("unique"))

    monkeypatch.setattr(db.session, "commit", lose_race)
    returned, created = meal_idempotency.commit_once(candidate, key)

    assert returned.id == winner.id
    assert created is False
