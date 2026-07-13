"""Tests for the FitX MCP server tools (fitx_mcp/server.py).

psycopg2 bağlantısı, sorgu sırasına göre satır döndüren sahte cursor ile
taklit edilir (Postgres yok); FatSecret çağrıları mock'lu. Okuma araçlarının
JSON sözleşmeleri, yazma araçlarının doğrulamaları ve haftalık rapor /
menü sıralama matematiği sabitlenir.

    python -m pytest tests/test_mcp_tools.py -v
"""
import json
import time
from contextlib import contextmanager
from datetime import datetime

import pytest

import fitx_mcp.server as server
from app.services import coach_context_queries


class _FakeCursor:
    """execute() çağrı sırasına göre önceden tanımlı satır kümeleri döndürür."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.executed = []
        self._current = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._current = self._batches.pop(0) if self._batches else []

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return self._current


@pytest.fixture
def fake_db(monkeypatch):
    """get_conn/get_write_conn'u verilen satır kümeleriyle değiştirir."""
    state = {}

    def install(*batches):
        cursor = _FakeCursor(batches)
        state["cursor"] = cursor

        @contextmanager
        def fake_conn():
            yield type("Conn", (), {"cursor": lambda self: cursor})()

        monkeypatch.setattr(server, "get_conn", fake_conn)
        monkeypatch.setattr(server, "get_write_conn", fake_conn)
        monkeypatch.setattr(coach_context_queries, "get_conn", fake_conn)
        return cursor

    return install


USER_ROW = {"username": "yusuf", "weight": 80.0, "height": 180.0, "age": 30,
            "gender": "male", "goal": "kas kazanma", "fitness_level": "beginner",
            "current_activity": "active", "streak_count": 4, "rank_points": 1200,
            "last_login": None}


# ---------------------------------------------------------------------------
# Okuma araçları
# ---------------------------------------------------------------------------

def test_fitness_summary_contract(fake_db):
    fake_db(
        [USER_ROW],
        [{"weight": 80, "target_calories": 3059.0, "bmr": 1780.0, "tdee": 2759.0,
          "goal": "kas kazanma", "fitness_level": "beginner", "created_at": datetime.utcnow()}],
        [{"weight": 80.0, "created_at": datetime(2026, 6, 10)}],
        [{"total_cal": 1450.0, "meal_count": 3}],
    )
    result = json.loads(server.get_user_fitness_summary(7))
    assert result["username"] == "yusuf"
    assert result["level"] == 3                      # 1200 XP → seviye 3
    assert result["title"] == "Fitness Yolcusu"
    assert result["xp_for_next_level"] == 300        # 500 - 1200%500
    assert result["target_calories"] == 3059
    assert result["today_calories_consumed"] == 1450
    assert result["recent_checkins"][0]["date"] == "2026-06-10"


def test_fitness_summary_unknown_user(fake_db):
    fake_db([])
    assert json.loads(server.get_user_fitness_summary(999))["error"] == "Kullanıcı bulunamadı"


def test_workout_history_clamps_days_and_parses_plans(fake_db):
    fake_db(
        [{"id": 7}],
        [{"plan_data": '{"program": []}', "score": 8.0, "created_at": datetime(2026, 6, 9)}],
        [{"date_key": "2026-06-10", "title": "Log a Workout"}],
    )
    result = json.loads(server.get_user_workout_history(7, days=500))
    assert result["period_days"] == 90               # 1..90 aralığına kısılır
    assert result["training_plans"][0]["plan"] == {"program": []}
    assert result["total_workouts_completed"] == 1


def test_supplement_stack_counts_active(fake_db):
    supp = {"product_name": "Whey", "brand": "ON", "category": "Protein",
            "status": "Active", "rating_effect": 5, "rating_taste": 4,
            "rating_digestion": None, "rating_price": 3, "review_text": "iyi",
            "price_paid": 900.0, "created_at": datetime(2026, 6, 1)}
    fake_db([{"id": 7}], [supp, dict(supp, product_name="Kreatin", status="Finished")])
    result = json.loads(server.get_user_supplement_stack(7))
    assert result["total_supplements"] == 2
    assert result["active_count"] == 1


def test_friend_activities_empty_and_populated(fake_db):
    fake_db([{"id": 7}], [])
    assert json.loads(server.get_friend_activities(7))["friends_count"] == 0

    fake_db(
        [{"id": 7}],
        [{"friend_id": 9}],
        [{"activity_type": "workout_completed", "content": "antrenman",
          "timestamp": datetime(2026, 6, 11, 9, 0), "username": "arkadas", "full_name": None}],
    )
    result = json.loads(server.get_friend_activities(7))
    assert result["friends_count"] == 1
    assert result["activities"][0]["name"] == "arkadas"


def test_nutrition_log_groups_by_day(fake_db):
    fake_db(
        [{"id": 7}],
        [{"ogun": "Kahvaltı", "yemekler": "yulaf", "kalori": 400.0, "protein": 20.0,
          "karb": 60.0, "yag": 8.0, "tarih": "11.06", "created_at": datetime(2026, 6, 11)},
         {"ogun": "Öğle", "yemekler": "tavuk", "kalori": 500.0, "protein": 45.0,
          "karb": 30.0, "yag": 12.0, "tarih": "11.06", "created_at": datetime(2026, 6, 11)}],
        [{"plan_data": '{"kahvalti": {}}', "score": 7.5, "created_at": datetime(2026, 6, 1)}],
    )
    result = json.loads(server.get_user_nutrition_log(7, days=3))
    assert result["total_meals_logged"] == 2
    assert result["daily_logs"]["11.06"]["total_cal"] == 900.0
    assert result["active_plan"] == {"kahvalti": {}}


# ---------------------------------------------------------------------------
# Yazma araçları — doğrulama + RETURNING akışı
# ---------------------------------------------------------------------------

def test_log_workout_entry_validates_then_inserts(fake_db):
    bad = json.loads(server.log_workout_entry(7, "Squat", 0, 10, 100))
    assert "Geçersiz" in bad["error"]
    bad = json.loads(server.log_workout_entry(7, "  ", 3, 10, 100))
    assert "boş" in bad["error"]

    cursor = fake_db([{"id": 7}], [{"id": 55}],
                     [{"total_volume": 2500.0, "entry_count": 1}])
    result = json.loads(server.log_workout_entry(7, "Squat", 5, 5, 100))
    assert result["success"] is True
    assert result["volume"] == 2500
    assert result["today_total_volume"] == 2500
    insert_sql, insert_params = cursor.executed[1]
    assert "INSERT INTO workout_log" in insert_sql
    assert insert_params[0] == 7                      # user_id parametreli (injection yok)


def test_log_nutrition_entry_validates_then_inserts(fake_db):
    bad = json.loads(server.log_nutrition_entry(7, "muz", -5, 1, 23, 0.3))
    assert "negatif" in bad["error"]

    fake_db([{"id": 7}], [{"id": 56}],
            [{"total_cal": 489.0, "total_protein": 62.1, "total_carbs": 23.0,
              "total_fat": 7.3, "entry_count": 2}])
    result = json.loads(server.log_nutrition_entry(7, "muz", 89, 1.1, 23, 0.3))
    assert result["success"] is True
    assert result["today_totals"]["calories"] == 489


def test_write_tools_reject_unknown_user(fake_db):
    fake_db([])
    assert "bulunamadı" in json.loads(server.log_workout_entry(999, "Squat", 3, 10, 60))["error"]
    fake_db([])
    assert "bulunamadı" in json.loads(server.log_nutrition_entry(999, "muz", 89, 1, 23, 0.3))["error"]


# ---------------------------------------------------------------------------
# Haftalık rapor
# ---------------------------------------------------------------------------

def test_weekly_report_compares_weeks(fake_db):
    fake_db(
        [{"id": 7, "goal": "kas kazanma"}],
        [{"exercise_name": "Squat", "total_vol": 5000.0, "total_sets": 15,
          "entries": 3, "max_weight": 120.0},
         {"exercise_name": "Bench", "total_vol": 3000.0, "total_sets": 12,
          "entries": 3, "max_weight": 90.0}],
        [{"vol": 6400.0}],
        [{"cal": 14000.0, "pro": 980.0, "carb": 1400.0, "fat": 420.0, "entries": 21}],
        [{"cal": 13000.0, "pro": 900.0}],
    )
    result = json.loads(server.generate_weekly_report(7))
    workouts = result["workouts"]
    assert workouts["total_volume"] == 8000
    assert workouts["volume_change"] == 1600
    assert workouts["volume_change_pct"] == 25.0
    assert workouts["mvp_exercise"]["exercise"] == "Squat"
    assert result["nutrition"]["calorie_change"] == 1000


def test_marker_constant_matches_app_models():
    # fitx_mcp bağımsız çalışır (app import etmez); sabit kopyası D4
    # sözleşmesinden (app/models.py) sapmamalı.
    from app.models import WORKOUT_COMPLETION_MARKER
    assert server.WORKOUT_COMPLETION_MARKER == WORKOUT_COMPLETION_MARKER


def test_workout_aggregations_exclude_pump_check_marker(fake_db):
    # B1: sentetik Pump-Check satırı (volume=0) gerçek egzersiz değildir; UI'dan
    # yalnız Pump Check ile tamamlayan kullanıcıda koçun egzersiz dökümü tamamen
    # bu hayalet satırdan oluşuyordu. Uygulama-içi toplulaştırmalar (D4) dışlar,
    # MCP sorguları da dışlamalı.
    cursor = fake_db([{"id": 7}], [{"id": 55}],
                     [{"total_volume": 2500.0, "entry_count": 1}])
    server.log_workout_entry(7, "Squat", 5, 5, 100)
    today_sql, today_params = cursor.executed[2]
    assert "exercise_name <> %s" in today_sql
    assert server.WORKOUT_COMPLETION_MARKER in today_params

    cursor = fake_db(
        [{"id": 7, "goal": "kas kazanma"}],
        [{"exercise_name": "Squat", "total_vol": 5000.0, "total_sets": 15,
          "entries": 3, "max_weight": 120.0}],
        [{"vol": 6400.0}],
        [{"cal": 14000.0, "pro": 980.0, "carb": 1400.0, "fat": 420.0, "entries": 21}],
        [{"cal": 13000.0, "pro": 900.0}],
    )
    server.generate_weekly_report(7)
    weekly_sql, weekly_params = cursor.executed[1]
    assert "exercise_name <> %s" in weekly_sql
    assert server.WORKOUT_COMPLETION_MARKER in weekly_params
    lastweek_sql, lastweek_params = cursor.executed[2]
    assert "exercise_name <> %s" in lastweek_sql
    assert server.WORKOUT_COMPLETION_MARKER in lastweek_params


# ---------------------------------------------------------------------------
# FatSecret araçları
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


@pytest.fixture
def fs_token():
    server._fs_token_cache.update({"token": "tok", "expires_at": time.time() + 3600})
    yield
    server._fs_token_cache.update({"token": None, "expires_at": 0})


def test_search_nutrition_data(fs_token, monkeypatch):
    assert "boş" in json.loads(server.search_nutrition_data("  "))["error"]

    payload = {"foods": {"food": {"food_name": "Banana", "food_type": "Generic",
                                  "food_description": "Per 100g - Calories: 89kcal | "
                                                      "Fat: 0.3g | Carbs: 23g | Protein: 1.1g"}}}
    monkeypatch.setattr(server.requests, "get", lambda *a, **kw: _Resp(payload))
    result = json.loads(server.search_nutrition_data("muz"))
    assert result["results"][0]["name"] == "Banana"
    assert result["results"][0]["per_serving"]["calories"] == 89.0

    monkeypatch.setattr(server.requests, "get", lambda *a, **kw: _Resp({"foods": {}}))
    assert json.loads(server.search_nutrition_data("zzz"))["results"] == []


def test_search_nutrition_data_auth_failure(monkeypatch):
    monkeypatch.delenv("FATSECRET_CLIENT_ID", raising=False)
    monkeypatch.delenv("FATSECRET_CLIENT_SECRET", raising=False)
    assert "auth hatası" in json.loads(server.search_nutrition_data("muz"))["error"]


def test_analyze_and_rank_menu_filters_marketing_and_ranks(fake_db, fs_token, monkeypatch):
    fake_db(
        [{"target_calories": 2000.0, "goal": "kas kazanma"}],
        [{"cal": 500.0, "pro": 30.0, "carb": 50.0, "fat": 15.0}],
    )
    descs = {
        "Izgara Tavuk": "Per 1 serving - Calories: 330kcal | Fat: 7g | Carbs: 0g | Protein: 62g",
        "Künefe": "Per 1 serving - Calories: 800kcal | Fat: 40g | Carbs: 95g | Protein: 12g",
    }

    def fake_get(url, params=None, **kwargs):
        q = params["search_expression"]
        if q in descs:
            return _Resp({"foods": {"food": {"food_name": q, "food_description": descs[q]}}})
        return _Resp({"foods": {}})
    monkeypatch.setattr(server.requests, "get", fake_get)

    menu = "Izgara Tavuk\nKünefe\nKampanya: 2 al 1 öde\nFiyat listesi TL\n"
    result = json.loads(server.analyze_and_rank_menu(menu, 7))
    names = [i["name"] for i in result["ranked_items"]]
    assert names[0] == "Izgara Tavuk"                # protein uyumu kazanır
    assert "Kampanya: 2 al 1 öde" not in names       # pazarlama satırı elendi
    assert result["remaining_macros"]["calories"] == 1500.0


def test_analyze_and_rank_menu_requires_profile(fake_db):
    fake_db([])
    assert "profil" in json.loads(server.analyze_and_rank_menu("menü", 7))["error"].lower()


# ---------------------------------------------------------------------------
# Saf yardımcılar
# ---------------------------------------------------------------------------

def test_level_and_title_helpers():
    assert server._level(0) == 1
    assert server._level(1200) == 3
    assert server._title(51) == "Antrenman Tanrısı"


def test_utc_day_bounds_maps_istanbul_day():
    from datetime import date, datetime, timedelta

    # Istanbul UTC+3 (DST yok): 2026-06-15 Istanbul günü → [06-14 21:00, 06-15 21:00) UTC.
    start, end = server._utc_day_bounds(date(2026, 6, 15))
    assert start == datetime(2026, 6, 14, 21, 0)
    assert end == datetime(2026, 6, 15, 21, 0)
    assert start.tzinfo is None and end.tzinfo is None   # naive UTC (created_at ile uyumlu)
    assert end - start == timedelta(days=1)


def test_parse_fatsecret_desc():
    parsed = server._parse_fatsecret_desc(
        "Per 100g - Calories: 165kcal | Fat: 3,57g | Carbs: 0.00g | Protein: 31.02g")
    assert parsed["serving"] == "Per 100g"
    assert parsed["fat"] == 3.57                     # virgül ondalığı
    assert server._parse_fatsecret_desc("") is None
    assert server._parse_fatsecret_desc("alakasız metin") is None
