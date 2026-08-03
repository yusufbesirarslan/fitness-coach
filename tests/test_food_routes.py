"""Route tests for the food blueprint (app/blueprints/food.py).

Arama/porsiyon uçları; FatSecret ve koç-arama katmanı monkeypatch'lidir
(ağ yok). Önbellek isabet/ıskalama yolları ve TR→EN yedek arama sabitlenir.

    python -m pytest tests/test_food_routes.py -v
"""
import threading

import pytest

from app.blueprints import food as food_bp
from app.extensions import db
from app.services import ai_gate, foodcache

BANANA = {"calories": 89.0, "protein": 1.1, "carbs": 23.0, "fat": 0.3}


@pytest.fixture(autouse=True)
def _clean_caches():
    foodcache._macro_cache.clear()
    foodcache._food_id_cache.clear()
    yield
    foodcache._macro_cache.clear()
    foodcache._food_id_cache.clear()


# ---------------------------------------------------------------------------
# /api/food/search
# ---------------------------------------------------------------------------

def test_search_query_too_short_returns_empty(client, auth_user):
    response = client.get("/api/food/search?q=a")
    assert response.get_json() == {"results": []}


def test_search_cache_hit_skips_lookup(client, auth_user, monkeypatch):
    # Koç araması 100g-bazlı yazar; arama da o bazdan okur. (Eski test düz
    # _macro_cache["muz"] yazıp düz .get(q) hatasını maskeliyordu — F2.)
    foodcache._cache_macros({"muz": BANANA}, "per_100g")
    foodcache._food_id_cache["muz"] = "42"

    def boom(q):
        raise AssertionError("önbellek isabetinde arama çağrılmamalı")
    monkeypatch.setattr(food_bp, "_coach_search_food", boom)

    results = client.get("/api/food/search?q=muz").get_json()["results"]
    assert results == [{"name": "muz", "brand": "", "food_id": "42", "serving": "",
                        "is_per_serving": False, "macros": BANANA, "per_100g": BANANA}]


def test_search_cache_miss_uses_coach_search(client, auth_user, monkeypatch):
    canned = [{"name": "Banana", "per_100g": BANANA}]
    monkeypatch.setattr(food_bp, "_coach_search_food", lambda q: canned)
    assert client.get("/api/food/search?q=muz").get_json()["results"] == canned


def test_search_cache_miss_saturation_returns_empty_without_lookup(
        client, auth_user, monkeypatch):
    """A missing shared admission must preserve the food route's empty fallback."""
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    calls = 0

    def unexpected_lookup(_query):
        nonlocal calls
        calls += 1
        raise AssertionError("provider lookup must not start after saturation")

    monkeypatch.setattr(food_bp, "_coach_search_food", unexpected_lookup)
    assert semaphore.acquire(blocking=False)
    try:
        response = client.get("/api/food/search?q=muz")
    finally:
        semaphore.release()

    assert response.status_code == 200
    assert response.get_json() == {"results": []}
    assert calls == 0


def test_search_cache_hit_bypasses_saturated_shared_gate(
        client, auth_user, monkeypatch):
    """Putting the gate around the whole route would reject cheap cached work."""
    foodcache._cache_macros({"muz": BANANA}, "per_100g")
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    monkeypatch.setattr(
        food_bp, "_coach_search_food",
        lambda _query: (_ for _ in ()).throw(
            AssertionError("cache hit must not call the provider")),
    )

    assert semaphore.acquire(blocking=False)
    try:
        response = client.get("/api/food/search?q=muz")
    finally:
        semaphore.release()

    assert response.status_code == 200
    assert response.get_json()["results"][0]["macros"] == BANANA


def test_search_gate_and_provider_run_without_database_transaction(
        client, auth_user, monkeypatch):
    """Authentication reads must not leave a DB transaction around provider I/O."""
    class TransactionCheckingSemaphore:
        def __init__(self):
            self._semaphore = threading.BoundedSemaphore(1)
            self.acquisitions = 0

        def acquire(self, *, timeout):
            assert db.session().in_transaction() is False
            self.acquisitions += 1
            return self._semaphore.acquire(timeout=timeout)

        def release(self):
            self._semaphore.release()

    semaphore = TransactionCheckingSemaphore()
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)

    def lookup(_query):
        assert db.session().in_transaction() is False
        return [{"name": "Banana", "per_100g": BANANA}]

    monkeypatch.setattr(food_bp, "_coach_search_food", lookup)
    response = client.get("/api/food/search?q=muz")

    assert response.status_code == 200
    assert semaphore.acquisitions == 1


# ---------------------------------------------------------------------------
# /api/food/<id>/servings
# ---------------------------------------------------------------------------

def test_servings_found(client, auth_user, monkeypatch):
    servings = [{"serving_description": "100 g", "calories": 89.0}]
    monkeypatch.setattr(food_bp, "_food_get_servings", lambda fid: servings)
    assert client.get("/api/food/42/servings").get_json() == {"servings": servings}


def test_servings_missing_returns_debug_marker(client, auth_user, monkeypatch):
    monkeypatch.setattr(food_bp, "_food_get_servings", lambda fid: None)
    body = client.get("/api/food/42/servings").get_json()
    assert body["servings"] == []
    assert body["debug"] == "no_servings_returned"


# ---------------------------------------------------------------------------
# /api/food/servings-by-name
# ---------------------------------------------------------------------------

def test_servings_by_name_short_query(client, auth_user):
    body = client.get("/api/food/servings-by-name?name=a").get_json()
    assert body == {"servings": [], "food_id": ""}


def test_servings_by_name_cached_food_id(client, auth_user, monkeypatch):
    foodcache._food_id_cache["muz"] = "42"
    servings = [{"serving_description": "100 g"}]
    monkeypatch.setattr(food_bp, "_food_get_servings",
                        lambda fid: servings if fid == "42" else None)
    body = client.get("/api/food/servings-by-name?name=Muz").get_json()
    assert body == {"servings": servings, "food_id": "42"}


def test_servings_by_name_falls_back_to_english_search(client, auth_user, monkeypatch):
    # Türkçe terim sonuç vermez; İngilizce çeviri ('muz' → 'banana') bulur.
    # N3: artık ham results[0] yerine alaka-kapılı _fs_relevant_candidates kullanılır;
    # rota ham/İngilizce terimleri (name, english) o katmana geçirir.
    monkeypatch.setattr(food_bp, "_get_fatsecret_token", lambda: "tok")

    def fake_relevant(name, english, token):
        # Ham Türkçe sorgu ('muz') alaka-kapısından geçmez; İngilizce terim eşleşir.
        if english == "banana":
            return [{"food_id": "42", "food_name": "Banana"}]
        return []

    monkeypatch.setattr(food_bp, "_fs_relevant_candidates", fake_relevant)
    servings = [{"serving_description": "100 g"}]
    monkeypatch.setattr(food_bp, "_food_get_servings",
                        lambda fid: servings if fid == "42" else None)

    body = client.get("/api/food/servings-by-name?name=muz").get_json()
    assert body == {"servings": servings, "food_id": "42"}
    assert foodcache._food_id_cache["muz"] == "42"  # doğrulanan id önbelleğe yazıldı


def test_servings_by_name_no_relevant_match_returns_empty(client, auth_user, monkeypatch):
    # N3: alaka-kapısı hiçbir spesifik aday bulamazsa (ör. ilgisiz jenerik elenir)
    # rota boş döner ve GLOBAL food_id önbelleğine yanlış eşleşme YAZILMAZ.
    monkeypatch.setattr(food_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(food_bp, "_fs_relevant_candidates",
                        lambda name, english, token: [])
    body = client.get("/api/food/servings-by-name?name=muz").get_json()
    assert body == {"servings": [], "food_id": ""}
    assert "muz" not in foodcache._food_id_cache


def test_servings_by_name_token_failure_returns_empty(client, auth_user, monkeypatch):
    def boom():
        raise RuntimeError("no creds")
    monkeypatch.setattr(food_bp, "_get_fatsecret_token", boom)
    body = client.get("/api/food/servings-by-name?name=muz").get_json()
    assert body == {"servings": [], "food_id": ""}


def test_food_routes_require_login(client):
    assert client.get("/api/food/search?q=muz").status_code == 302
    assert client.get("/api/food/42/servings").status_code == 302
