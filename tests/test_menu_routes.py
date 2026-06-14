"""Route tests for the menu blueprint (app/blueprints/menu.py).

/api/proxy/scan-menu (SSRF doğrulamalı tarayıcı + Drive yönlendirmesi) ve
/api/menu/analyze (çıkarım → FatSecret → LLM yedeği → deterministik skor)
uçları. Ağ ve LLM katmanları mock'lu.

    python -m pytest tests/test_menu_routes.py -v
"""
import pytest
import requests

from app.blueprints import menu as menu_bp
from app.extensions import db
from app.models import MealLog, UserSession
from app.services import foodcache

MENU_HTML = """<html><head><title>Lezzet Durağı</title></head><body>
<h2>Çorbalar</h2><li>Mercimek Çorbası</li><li>45</li>
<h2>Ana Yemekler</h2><li>Adana Kebap</li><li>Izgara Tavuk</li>
<li>Margherita Pizza</li><li>Ev Burger</li>
</body></html>"""


class _Resp:
    def __init__(self, body=MENU_HTML, status=200, content_type="text/html; charset=utf-8"):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.text = body


@pytest.fixture(autouse=True)
def _clean_macro_cache():
    foodcache._macro_cache.clear()
    yield
    foodcache._macro_cache.clear()


# ---------------------------------------------------------------------------
# /api/proxy/scan-menu
# ---------------------------------------------------------------------------

def test_scan_requires_url(client, auth_user):
    assert client.post("/api/proxy/scan-menu", json={}).status_code == 400


def test_scan_blocks_internal_url(client, auth_user):
    response = client.post("/api/proxy/scan-menu", json={"url": "http://127.0.0.1/admin"})
    assert response.status_code == 400
    assert "İç ağ" in response.get_json()["error"]


def test_scan_scrapes_sections_and_headings(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    monkeypatch.setattr(menu_bp, "_fetch_page", lambda url, timeout=10: _Resp())

    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://restoran.example/menu"}).get_json()
    assert body["title"] == "Lezzet Durağı"
    assert "Çorbalar" in body["headings"]
    assert "Adana Kebap" in body["body_text"]
    assert "45" not in body["body_text"].split()      # fiyat gürültüsü düştü
    assert body["menu_source"] == "web_scraper"


def test_scan_error_mapping(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))

    def raise_(exc):
        def _f(url, timeout=10):
            raise exc
        return _f

    monkeypatch.setattr(menu_bp, "_fetch_page", raise_(requests.exceptions.Timeout()))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 504

    monkeypatch.setattr(menu_bp, "_fetch_page",
                        raise_(requests.exceptions.ConnectionError()))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 502

    monkeypatch.setattr(menu_bp, "_fetch_page",
                        raise_(ValueError("İç ağ adresleri engellendi.")))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 400

    monkeypatch.setattr(menu_bp, "_fetch_page",
                        lambda url, timeout=10: _Resp(content_type="application/pdf"))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 415


def test_scan_unreadable_page_returns_422(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_bp, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    monkeypatch.setattr(menu_bp, "_fetch_page",
                        lambda url, timeout=10: _Resp(body="<html><body></body></html>"))
    monkeypatch.setattr(menu_bp, "_try_wordpress_api", lambda base, html: (None, []))
    response = client.post("/api/proxy/scan-menu", json={"url": "https://bos.example"})
    assert response.status_code == 422


def test_scan_routes_drive_urls(client, auth_user, monkeypatch):
    result = {"title": "Drive Menü", "body_text": "Kebap", "menu_source": "google_drive"}
    monkeypatch.setattr(menu_bp, "_process_google_drive_url", lambda url: (result, None))
    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://drive.google.com/file/d/X/view"}).get_json()
    assert body["menu_source"] == "google_drive"

    # Yapısal (JSON) hata → 403, düz metin hata → 422.
    monkeypatch.setattr(menu_bp, "_process_google_drive_url",
                        lambda url: (None, '{"error": "GOOGLE_DRIVE_LINK_RESTRICTED"}'))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://drive.google.com/file/d/X/view"}).status_code == 403

    monkeypatch.setattr(menu_bp, "_process_google_drive_url",
                        lambda url: (None, "PDF bozuk"))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://drive.google.com/file/d/X/view"}).status_code == 422


# ---------------------------------------------------------------------------
# /api/menu/analyze
# ---------------------------------------------------------------------------

MENU_TEXT = "Çorbalar: Mercimek Çorbası... Ana Yemekler: Adana Kebap, Izgara Tavuk"
CHICKEN = {"calories": 330.0, "protein": 62.0, "carbs": 0.0, "fat": 7.0}


@pytest.fixture
def profile_session(auth_user):
    db.session.add(UserSession(user_id=auth_user.id, target_calories=2000,
                               goal="kas kazanma"))
    db.session.commit()
    return auth_user


def _mock_pipeline(monkeypatch, categorized, per_serving=None, llm=None):
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: categorized)
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: (per_serving or {}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None: llm or {})


def test_analyze_requires_text_and_profile(client, auth_user):
    assert client.post("/api/menu/analyze", json={"menu_text": "kısa"}).status_code == 400
    response = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert response.status_code == 400  # UserSession yok
    assert "Profil" in response.get_json()["error"]


def test_analyze_scores_items_and_computes_budget(client, profile_session, monkeypatch):
    # Bugün 500 kcal yenmiş — kalan bütçeye yansımalı.
    db.session.add(MealLog(user_id=profile_session.id, ogun="Kahvaltı", yemekler="x",
                           kalori=500, protein=30, karb=50, yag=15,
                           tarih=__import__("datetime").datetime.utcnow().strftime("%d.%m")))
    db.session.commit()
    _mock_pipeline(monkeypatch, {"Ana Yemekler": ["Izgara Tavuk"]},
                   per_serving={"Izgara Tavuk": CHICKEN})

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["success"] is True
    assert body["target"] == {"calories": 2000, "protein": 150, "carbs": 225, "fat": 56}
    assert body["consumed"]["calories"] == 500
    assert body["remaining"]["calories"] == 1500

    item = body["categories"]["Ana Yemekler"][0]
    assert item["name"] == "Izgara Tavuk"
    assert item["macros"]["protein"] == 62
    assert body["coach_picks"][0]["name"] == "Izgara Tavuk"


def test_analyze_discards_thermodynamically_impossible_items(client, profile_session, monkeypatch):
    _mock_pipeline(monkeypatch, {"Ana Yemekler": ["Normal Yemek", "İmkansız Yemek"]},
                   per_serving={
                       "Normal Yemek": CHICKEN,
                       "İmkansız Yemek": {"calories": 9000.0, "protein": 500.0,
                                          "carbs": 500.0, "fat": 500.0},
                   })
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    names = [i["name"] for i in body["items"]]
    assert "Normal Yemek" in names
    assert "İmkansız Yemek" not in names  # yanıttan tamamen elendi


def test_analyze_zero_macro_items_kept_with_zero_score(client, profile_session, monkeypatch):
    _mock_pipeline(monkeypatch, {"Tatlılar": ["Gizemli Tatlı"]})  # hiçbir kaynak bulamadı
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Tatlılar"][0]
    assert item["score"] == 0
    assert item["reason"] == "Besin değerleri hesaplanamadı"
    assert body["coach_picks"] == []


def test_analyze_extraction_failure_returns_parsing_error(client, profile_session, monkeypatch):
    calls = []

    def failing_extract(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("LLM çöktü")
    monkeypatch.setattr(menu_bp, "_extract_categorized_items", failing_extract)

    response = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "OUTPUT_PARSING_FAILED"
    assert len(calls) == 2  # framework_state'siz ikinci deneme yapıldı


def test_analyze_serves_cached_items_without_fatsecret(client, profile_session, monkeypatch):
    # Menü hattı PORSİYON-bazlı namespace'ten okur (per-100g koç önbelleğiyle karışmaz).
    foodcache._macro_cache.setdefault("per_serving", {})["Izgara Tavuk"] = CHICKEN
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk"]})

    def boom():
        raise AssertionError("tam önbellek isabetinde FatSecret çağrılmamalı")
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", boom)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["success"] is True
    assert body["items"][0]["macros"]["calories"] == 330


def test_analyze_llm_fallback_for_missing_items(client, profile_session, monkeypatch):
    _mock_pipeline(monkeypatch, {"Ana Yemekler": ["Bilinmeyen Yemek"]},
                   per_serving={}, llm={"Bilinmeyen Yemek": CHICKEN})
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["items"][0]["macros"]["calories"] == 330


# ---------------------------------------------------------------------------
# Beyan-gramaj yeniden-tahmin geçişi (evrensel restoran porsiyonu)
# ---------------------------------------------------------------------------

FAJITA = "Tavuklu Fajita (220 GR)"
FAJITA_LOW = {"calories": 125.0, "protein": 18.0, "carbs": 9.0, "fat": 2.0}      # 0.57 kcal/g
FAJITA_FIXED = {"calories": 430.0, "protein": 32.0, "carbs": 28.0, "fat": 18.0}  # gerçekçi


def test_analyze_reestimates_low_density_stated_gram_item(client, profile_session, monkeypatch):
    # 220g fajita FatSecret'tan 125 kcal geldi (imkânsız düşük); gramaj ipucuyla
    # yeniden tahmin gerçekçi değere yükseltmeli.
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Fajitalar": [FAJITA]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: ({FAJITA: FAJITA_LOW}, {}))

    seen = {}

    def fake_llm(items, category_map=None, grams_hint=None):
        seen["grams_hint"] = grams_hint
        return {FAJITA: FAJITA_FIXED}
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm", fake_llm)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Fajitalar"][0]
    assert item["macros"]["calories"] == 430        # yeniden tahmin uygulandı
    assert seen["grams_hint"] == {FAJITA: 220.0}     # gramaj otorite ipucu olarak geçti


def test_analyze_keeps_original_when_reestimate_not_better(client, profile_session, monkeypatch):
    # Yeniden tahmin hâlâ imkânsız-düşükse özgün değer korunur (asla kötüleştirme).
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Fajitalar": [FAJITA]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: ({FAJITA: FAJITA_LOW}, {}))
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm",
                        lambda items, category_map=None, grams_hint=None: {FAJITA: {"calories": 90.0, "protein": 10, "carbs": 6, "fat": 1}})

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Fajitalar"][0]
    assert item["macros"]["calories"] == 125        # özgün değer korundu


def test_analyze_does_not_reestimate_dense_stated_gram_item(client, profile_session, monkeypatch):
    # Gerçekçi yoğunluktaki gramajlı öğe (160g burger, 736 kcal) yeniden tahmin EDİLMEZ.
    burger = "BBQ & Cheddar Burger (160 GR)"
    monkeypatch.setattr(menu_bp, "_extract_categorized_items",
                        lambda *a, **kw: {"Burgerler": [burger]})
    monkeypatch.setattr(menu_bp, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_bp, "_lookup_macros_fatsecret",
                        lambda items, token, cmap=None: ({burger: {"calories": 736.0, "protein": 42, "carbs": 34, "fat": 28}}, {}))

    def boom(items, category_map=None, grams_hint=None):
        raise AssertionError("yoğunluğu gerçekçi öğe yeniden tahmin edilmemeli")
    monkeypatch.setattr(menu_bp, "_estimate_macros_llm", boom)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["categories"]["Burgerler"][0]["macros"]["calories"] == 736
