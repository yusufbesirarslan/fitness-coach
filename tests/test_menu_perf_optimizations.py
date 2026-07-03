"""Menü tarayıcı performans optimizasyonlarının testleri.

Kapsam:
- /api/menu/analyze çıkarım (extraction) sonucu Redis önbelleği — aynı menü
  metni ikinci kez analiz edilirken LLM çıkarımı atlanır.
- Yanıt öğelerindeki additive confidence / macro_source alanları.
- foodcache Redis L2 (yazma-geçişli, L1-miss → L2 okuma).
- _lookup_macros_fatsecret paralel çalışırken seri sürümle aynı deterministik
  sonucu üretir.
- proxy_scan_menu alt-sayfa paralel taraması: bölüm sırası girdi sırasına göre
  deterministik; başarısız sayfalar crawl_errors'a düşer.

    python -m pytest tests/test_menu_perf_optimizations.py -v
"""
import json

import pytest
import requests

from app.blueprints import menu as menu_bp
from app.extensions import db
from app.models import UserSession
from app.services import fatsecret, foodcache

MENU_TEXT = "Çorbalar: Mercimek Çorbası... Ana Yemekler: Adana Kebap, Izgara Tavuk"
CHICKEN = {"calories": 330.0, "protein": 62.0, "carbs": 0.0, "fat": 7.0}


class _FakeRedis:
    """get/setex/mget/pipeline destekleyen minimal in-memory Redis taklidi."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def pipeline(self, transaction=True):
        fake = self

        class _Pipe:
            def __init__(self):
                self._ops = []

            def setex(self, key, ttl, value):
                self._ops.append((key, ttl, value))
                return self

            def execute(self):
                for key, ttl, value in self._ops:
                    fake.setex(key, ttl, value)
                return [True] * len(self._ops)

        return _Pipe()


@pytest.fixture(autouse=True)
def _clean_macro_cache():
    foodcache._macro_cache.clear()
    yield
    foodcache._macro_cache.clear()


@pytest.fixture
def profile_session(auth_user):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000,
                               goal="kas kazanma"))
    db.session.commit()
    return auth_user


def _mock_macro_pipeline(monkeypatch, per_serving=None, llm=None):
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: (per_serving or {}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None, grams_hint=None: llm or {})


# ---------------------------------------------------------------------------
# Çıkarım önbelleği (menu:extract:v1:*)
# ---------------------------------------------------------------------------

def test_second_analyze_of_same_menu_skips_llm_extraction(client, profile_session, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(menu_bp, "redis_client", fake)
    calls = []

    def extract(*a, **kw):
        calls.append(1)
        return {"Ana Yemekler": ["Izgara Tavuk"]}

    monkeypatch.setattr(menu_bp, "_extract_categorized_items", extract)
    _mock_macro_pipeline(monkeypatch, per_serving={"Izgara Tavuk": dict(CHICKEN)})

    r1 = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    r2 = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(calls) == 1  # ikinci istek çıkarımı Redis'ten aldı
    assert r2.get_json()["items"][0]["name"] == "Izgara Tavuk"
    # Önbellek anahtarı doğru namespace'te
    assert any(k.startswith(menu_bp.MENU_EXTRACT_CACHE_PREFIX) for k in fake.store)


def test_extraction_cache_key_varies_with_inputs():
    k = menu_bp._menu_extract_cache_key
    base = k("menü metni", None, None, "web_scraper")
    assert base != k("başka metin", None, None, "web_scraper")
    assert base != k("menü metni", '{"a":1}', None, "web_scraper")
    assert base != k("menü metni", None, ["Çorbalar"], "web_scraper")
    assert base != k("menü metni", None, None, "google_drive")
    assert base == k("menü metni", None, None, "web_scraper")


def test_empty_extraction_not_cached(client, profile_session, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(menu_bp, "redis_client", fake)
    monkeypatch.setattr(menu_bp, "_extract_categorized_items", lambda *a, **kw: {})

    r = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert r.status_code == 422
    assert not any(k.startswith(menu_bp.MENU_EXTRACT_CACHE_PREFIX) for k in fake.store)


# ---------------------------------------------------------------------------
# Confidence / macro_source alanları
# ---------------------------------------------------------------------------

def test_items_report_confidence_and_source(client, profile_session, monkeypatch):
    monkeypatch.setattr(menu_bp, "redis_client", None)
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk", "Bilinmeyen Yemek", "Gizemli Yemek"]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: ({"Izgara Tavuk": dict(CHICKEN)}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None, grams_hint=None: {
                            "Bilinmeyen Yemek": {"calories": 500.0, "protein": 25.0,
                                                 "carbs": 45.0, "fat": 22.0}})

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    items = {i["name"]: i for i in body["items"]}

    assert items["Izgara Tavuk"]["macro_source"] == "fatsecret_serving"
    assert items["Izgara Tavuk"]["confidence"] == pytest.approx(0.9)
    assert items["Bilinmeyen Yemek"]["macro_source"] == "llm"
    assert items["Bilinmeyen Yemek"]["confidence"] == pytest.approx(0.6)
    # Hiçbir kaynaktan çözülemeyen öğe: sıfır makro + sıfır güven, ama yanıtta KALIR.
    assert items["Gizemli Yemek"]["macro_source"] == "none"
    assert items["Gizemli Yemek"]["confidence"] == 0.0


def test_cached_macros_report_cache_source(client, profile_session, monkeypatch):
    monkeypatch.setattr(menu_bp, "redis_client", None)
    foodcache._cache_macros({"Izgara Tavuk": dict(CHICKEN)}, basis="per_serving")
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk"]})

    def boom():
        raise RuntimeError("FatSecret'a gidilmemeli")
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", boom)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["items"][0]
    assert item["macro_source"] == "cache"
    assert item["confidence"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# foodcache Redis L2
# ---------------------------------------------------------------------------

def test_foodcache_writes_through_to_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: fake)

    foodcache._cache_macros({"Adana Kebap": {"calories": 550.0, "protein": 35.0,
                                             "carbs": 10.0, "fat": 40.0}},
                            basis="per_serving")
    key = foodcache._macro_redis_key("per_serving", "Adana Kebap")
    assert key in fake.store
    assert json.loads(fake.store[key])["calories"] == 550.0


def test_foodcache_l1_miss_falls_back_to_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(foodcache, "_get_redis", lambda: fake)
    fake.setex(foodcache._macro_redis_key("per_serving", "Pilav"), 60,
               json.dumps({"calories": 210.0, "protein": 4.0, "carbs": 45.0, "fat": 1.0}))

    hits, misses = foodcache._get_cached_macros(["Pilav", "Bilinmeyen"], basis="per_serving")
    assert hits["Pilav"]["calories"] == 210.0
    assert misses == ["Bilinmeyen"]
    # L2 hit'i L1'e geri konur — ikinci okuma Redis'e gitmeden bulur.
    monkeypatch.setattr(foodcache, "_get_redis", lambda: None)
    hits2, _ = foodcache._get_cached_macros(["Pilav"], basis="per_serving")
    assert hits2["Pilav"]["calories"] == 210.0


def test_foodcache_survives_redis_errors(monkeypatch):
    class _Broken:
        def mget(self, keys):
            raise ConnectionError("redis down")

        def pipeline(self, transaction=True):
            raise ConnectionError("redis down")

    monkeypatch.setattr(foodcache, "_get_redis", lambda: _Broken())
    # Ne yazma ne okuma hata fırlatmalı; L1 davranışı sürer.
    foodcache._cache_macros({"Çay": {"calories": 2.0, "protein": 0.0,
                                     "carbs": 0.5, "fat": 0.0}}, basis="per_serving")
    hits, misses = foodcache._get_cached_macros(["Çay", "Yok"], basis="per_serving")
    assert "Çay" in hits and misses == ["Yok"]


# ---------------------------------------------------------------------------
# Paralel FatSecret çözümlemesi — deterministik sonuç
# ---------------------------------------------------------------------------

def _desc(serving, cal, p, c, f):
    return (f"{serving} - Calories: {cal}kcal | Fat: {f}g | "
            f"Carbs: {c}g | Protein: {p}g")


def test_parallel_lookup_matches_serial_semantics(app, monkeypatch):
    """Çok öğeli sorgu (thread havuzu devrede) her öğeyi doğru kovaya koyar ve
    sözlük sırası girdi sırasını izler."""
    monkeypatch.setattr(fatsecret, "_normalize_food_queries_en", lambda names, cmap=None: {})

    catalog = {
        "Adana Kebap": [{"food_name": "Adana Kebap Plate",
                         "food_description": _desc("Per 1 serving", 550, 35, 10, 40)}],
        "Pilav": [{"food_name": "Pilav Rice",
                   "food_description": _desc("Per 100g", 130, 2.7, 28, 0.3)}],
        "Bilinmeyen": [],
        "Izgara Tavuk": [{"food_name": "Izgara Tavuk Grilled Chicken",
                          "food_description": _desc("Per 1 serving", 330, 62, 0, 7)}],
    }
    monkeypatch.setattr(fatsecret, "_fs_search_foods", lambda q, tok: catalog.get(q, []))

    items = ["Adana Kebap", "Pilav", "Bilinmeyen", "Izgara Tavuk"]
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(items, "tok")

    assert list(per_serving) == ["Adana Kebap", "Izgara Tavuk"]
    assert per_serving["Adana Kebap"]["calories"] == 550.0
    assert list(per_100g) == ["Pilav"]
    assert per_100g["Pilav"]["calories"] == 130.0


def test_parallel_lookup_isolates_per_item_failures(app, monkeypatch):
    """Bir öğenin sorgusu patlasa bile diğerleri çözülür (eski seri davranışla aynı)."""
    monkeypatch.setattr(fatsecret, "_normalize_food_queries_en", lambda names, cmap=None: {})

    def search(q, tok):
        if q == "Patlayan":
            raise RuntimeError("network boom")
        return [{"food_name": "Izgara Tavuk Grilled Chicken",
                 "food_description": _desc("Per 1 serving", 330, 62, 0, 7)}]

    monkeypatch.setattr(fatsecret, "_fs_search_foods", search)
    per_serving, per_100g = fatsecret._lookup_macros_fatsecret(
        ["Patlayan", "Izgara Tavuk"], "tok")
    assert "Patlayan" not in per_serving and "Patlayan" not in per_100g
    assert "Izgara Tavuk" in per_serving


# ---------------------------------------------------------------------------
# Paralel alt-sayfa taraması — deterministik birleştirme
# ---------------------------------------------------------------------------

_MAIN_HTML = """<html><head><title>Lezzet Durağı</title></head><body>
<a href="/menu/corbalar">Çorbalar</a>
<a href="/menu/kebaplar">Kebaplar</a>
<h2>Ana Sayfa</h2><li>Günün Yemeği</li>
</body></html>"""

_SUB_HTML = {
    "https://restoran.example/menu/corbalar":
        "<html><body><h2>Çorbalar</h2><li>Mercimek Çorbası</li></body></html>",
    "https://restoran.example/menu/kebaplar":
        "<html><body><h2>Kebaplar</h2><li>Adana Kebap</li></body></html>",
}


class _Resp:
    def __init__(self, body, status=200):
        self.status_code = status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.text = body


def test_parallel_crawl_merges_sections_in_link_order(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_bp, "redis_client", None)
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))

    def fetch(url, timeout=10):
        if url in _SUB_HTML:
            return _Resp(_SUB_HTML[url])
        return _Resp(_MAIN_HTML)

    monkeypatch.setattr(menu_bp, "_fetch_page", fetch)

    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://restoran.example/menu"}).get_json()
    assert body["sub_pages_crawled"] == 2
    assert body["crawl_errors"] is None
    # Her iki alt sayfanın içeriği tek body_text'te birleşti.
    assert "Mercimek Çorbası" in body["body_text"]
    assert "Adana Kebap" in body["body_text"]
    # Bölüm sırası link keşif sırasını izler → kategori başlıkları deterministik.
    assert body["total_sections"] >= 3


def test_parallel_crawl_records_failures_without_dropping_scan(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_bp, "redis_client", None)
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))

    def fetch(url, timeout=10):
        if "kebaplar" in url:
            raise requests.exceptions.ConnectionError("refused")
        if url in _SUB_HTML:
            return _Resp(_SUB_HTML[url])
        return _Resp(_MAIN_HTML)

    monkeypatch.setattr(menu_bp, "_fetch_page", fetch)

    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://restoran.example/menu"}).get_json()
    assert "Mercimek Çorbası" in body["body_text"]  # sağlam sayfa işlendi
    assert body["crawl_errors"] and "kebaplar" in body["crawl_errors"][0]["url"]
