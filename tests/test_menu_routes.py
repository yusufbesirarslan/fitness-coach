"""Route tests for the menu blueprint (app/blueprints/menu.py).

/api/proxy/scan-menu (SSRF doğrulamalı tarayıcı + Drive yönlendirmesi) ve
/api/menu/analyze (çıkarım → FatSecret → LLM yedeği → deterministik skor)
uçları. Ağ ve LLM katmanları mock'lu.

    python -m pytest tests/test_menu_routes.py -v
"""
import pytest
import requests

from app.services import menu_analysis
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

@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_requires_url(client, auth_user):
    assert client.post("/api/proxy/scan-menu", json={}).status_code == 400


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_blocks_internal_url(client, auth_user):
    response = client.post("/api/proxy/scan-menu", json={"url": "http://127.0.0.1/admin"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "MENU_DESTINATION_BLOCKED"


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_scrapes_sections_and_headings(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_analysis, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    monkeypatch.setattr(menu_analysis, "_fetch_page", lambda url, timeout=10: _Resp())

    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://restoran.example/menu"}).get_json()
    assert body["title"] == "Lezzet Durağı"
    assert "Çorbalar" in body["headings"]
    assert "Adana Kebap" in body["body_text"]
    assert "45" not in body["body_text"].split()      # fiyat gürültüsü düştü
    assert body["menu_source"] == "web_scraper"


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_error_mapping(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_analysis, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))

    def raise_(exc):
        def _f(url, timeout=10):
            raise exc
        return _f

    monkeypatch.setattr(menu_analysis, "_fetch_page", raise_(requests.exceptions.Timeout()))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 504

    monkeypatch.setattr(menu_analysis, "_fetch_page",
                        raise_(requests.exceptions.ConnectionError()))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 502

    monkeypatch.setattr(menu_analysis, "_fetch_page",
                        raise_(ValueError("İç ağ adresleri engellendi.")))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 400

    monkeypatch.setattr(menu_analysis, "_fetch_page",
                        lambda url, timeout=10: _Resp(content_type="application/pdf"))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://x.example"}).status_code == 415


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_unreadable_page_returns_422(client, auth_user, monkeypatch):
    monkeypatch.setattr(menu_analysis, "_validate_menu_url",
                        lambda url: (requests.utils.urlparse(url), url, None))
    monkeypatch.setattr(menu_analysis, "_fetch_page",
                        lambda url, timeout=10: _Resp(body="<html><body></body></html>"))
    monkeypatch.setattr(menu_analysis, "_try_wordpress_api", lambda base, html: (None, []))
    response = client.post("/api/proxy/scan-menu", json={"url": "https://bos.example"})
    assert response.status_code == 422


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_scan_routes_drive_urls(client, auth_user, monkeypatch):
    result = {"title": "Drive Menü", "body_text": "Kebap", "menu_source": "google_drive"}
    monkeypatch.setattr(menu_analysis, "_process_google_drive_url", lambda url: (result, None))
    body = client.post("/api/proxy/scan-menu",
                       json={"url": "https://drive.google.com/file/d/X/view"}).get_json()
    assert body["menu_source"] == "google_drive"

    # Yapısal (JSON) hata → 403, düz metin hata → 422.
    monkeypatch.setattr(menu_analysis, "_process_google_drive_url",
                        lambda url: (None, '{"error": "GOOGLE_DRIVE_LINK_RESTRICTED"}'))
    assert client.post("/api/proxy/scan-menu",
                       json={"url": "https://drive.google.com/file/d/X/view"}).status_code == 403

    monkeypatch.setattr(menu_analysis, "_process_google_drive_url",
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


def _forbidden_fatsecret(*a, **k):
    raise AssertionError("QR menu composition must never call FatSecret (advice fallback)")


def _mock_pipeline(monkeypatch, categorized, per_serving=None, llm=None):
    # FatSecret advice fallback: QR composition uses only independent estimates,
    # so per-serving fixture values arrive through the independent LLM seam and
    # every source-agnostic sanity gate below still sees them.
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **kw: categorized)
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", _forbidden_fatsecret)
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", _forbidden_fatsecret)
    values = {**(per_serving or {}), **(llm or {})}
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm",
                        lambda items, category_map=None, grams_hint=None:
                        {n: dict(values[n]) for n in items if n in values})


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_requires_text_and_profile(client, auth_user):
    assert client.post("/api/menu/analyze", json={"menu_text": "kısa"}).status_code == 400
    response = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    assert response.status_code == 400  # UserSession yok
    assert "Profil" in response.get_json()["error"]


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_scores_items_and_computes_budget(client, profile_session, monkeypatch):
    # Measured-empty day: the full personal budget scores and ranks dishes.
    _mock_pipeline(monkeypatch, {"Ana Yemekler": ["Izgara Tavuk"]},
                   per_serving={"Izgara Tavuk": CHICKEN})
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["success"] is True and body["advice_available"] is True
    assert body["target"] == {"calories": 2000, "protein": 150, "carbs": 225, "fat": 56}
    assert body["consumed"]["calories"] == 0
    assert body["remaining"] == body["target"]
    item = body["categories"]["Ana Yemekler"][0]
    assert item["name"] == "Izgara Tavuk"
    assert item["macros"]["protein"] == 62
    assert body["coach_picks"][0]["name"] == "Izgara Tavuk"


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_unproven_logged_intake_withholds_the_budget(client, profile_session, monkeypatch):
    # FatSecret advice fallback: 500 kcal logged today has no verified lineage, so
    # no consumed/remaining figure, score or pick is personalized from it — and
    # the unknown budget is null, never 0 or the untouched target.
    from app.timeutil import day_key
    db.session.add(MealLog(user_id=profile_session.id, ogun="Kahvaltı", yemekler="x",
                           kalori=500, protein=30, karb=50, yag=15, tarih=day_key()))
    db.session.commit()
    _mock_pipeline(monkeypatch, {"Ana Yemekler": ["Izgara Tavuk"]},
                   per_serving={"Izgara Tavuk": CHICKEN})
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["success"] is True and body["advice_available"] is False
    assert body["target"] == {"calories": 2000, "protein": 150, "carbs": 225, "fat": 56}
    assert body["consumed"] is None and body["remaining"] is None
    item = body["categories"]["Ana Yemekler"][0]
    assert item["macros"]["protein"] == 62           # independent estimate kept
    assert item["score"] is None
    assert body["coach_picks"] == []


@pytest.mark.usefixtures("synthetic_ai_layer")
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


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_zero_macro_items_kept_with_zero_score(client, profile_session, monkeypatch):
    _mock_pipeline(monkeypatch, {"Tatlılar": ["Gizemli Tatlı"]})  # hiçbir kaynak bulamadı
    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Tatlılar"][0]
    assert item["score"] == 0
    assert item["reason"] == "Besin değerleri hesaplanamadı"
    assert body["coach_picks"] == []


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_extraction_failure_returns_parsing_error(client, profile_session, monkeypatch):
    calls = []

    def failing_extract(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("LLM çöktü")
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items", failing_extract)

    response = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT})
    # B9: çıkarım başarısızlığı artık 422 (eskiden 200 idi → client retry/error
    # monitoring'i devre dışı bırakıyordu). Gövde aynı kalır (success:False + mesaj).
    assert response.status_code == 422
    body = response.get_json()
    assert body["success"] is False
    assert body["error"] == "OUTPUT_PARSING_FAILED"
    # fw_state gönderilmediğinde yeniden deneme YAPILMAZ: ikinci çağrı birebir
    # aynı girdiyle (temperature=0) aynı sonucu üretirdi — en pahalı LLM çağrısını
    # sebepsiz ikiye katlıyordu. Retry yalnızca fw_state İLE denenmişken anlamlı
    # (aşağıdaki test_analyze_retries_without_framework_state).
    assert len(calls) == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_retries_without_framework_state(client, profile_session, monkeypatch):
    """fw_state İLE yapılan ilk çıkarım boş dönerse, fw_state'siz İKİNCİ deneme
    yapılır (fw_state bazen çıkarımı zehirler); o da çalışırsa sonuç kullanılır."""
    calls = []

    def extract(raw_text, fw_state=None, headings=None, menu_source=None):
        calls.append(fw_state)
        if fw_state:
            return {}
        return {"Ana Yemekler": ["Izgara Tavuk"]}

    monkeypatch.setattr(menu_analysis, "_extract_categorized_items", extract)
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", lambda: "tok")
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret",
                        lambda names, tok, cmap=None: (
                            {"Izgara Tavuk": {"calories": 400, "protein": 45, "carbs": 5, "fat": 20}}, {}))
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm",
                        lambda names, cmap=None, grams_hint=None: {})

    response = client.post("/api/menu/analyze",
                           json={"menu_text": MENU_TEXT, "framework_state": '{"page": 1}'})
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert calls == ['{"page": 1}', None]  # 1. deneme fw_state ile, 2. deneme fw_state'siz


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_serves_cached_items_without_fatsecret(client, profile_session, monkeypatch):
    # QR reads only the HMAC-attested independent-estimate namespace; the shared
    # mixed-origin per-serving cache is never trusted (FatSecret advice fallback).
    from types import SimpleNamespace
    from app.services import fatsecret_boundary
    store = {}
    redis = SimpleNamespace(mget=lambda keys: [store.get(k) for k in keys],
                            pipeline=lambda transaction=False: SimpleNamespace(
                                setex=lambda k, ttl, v: store.__setitem__(k, v), execute=lambda: []))
    monkeypatch.setattr(foodcache, "_get_redis", lambda: redis)
    fatsecret_boundary.cache_estimates({"Izgara Tavuk": dict(CHICKEN)})
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **kw: {"Ana Yemekler": ["Izgara Tavuk"]})

    def boom(*a, **k):
        raise AssertionError("tam önbellek isabetinde FatSecret/LLM çağrılmamalı")
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", boom)
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", boom)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["success"] is True
    assert body["items"][0]["macros"]["calories"] == 330


@pytest.mark.usefixtures("synthetic_ai_layer")
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


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_reestimates_low_density_stated_gram_item(client, profile_session, monkeypatch):
    # 220g fajita first estimated at 125 kcal (implausibly low); the stated-gram
    # hint must raise it. Both passes are independent estimates (no FatSecret).
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **kw: {"Fajitalar": [FAJITA]})
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", _forbidden_fatsecret)
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", _forbidden_fatsecret)

    seen = {}

    def fake_llm(items, category_map=None, grams_hint=None):
        if grams_hint is None:
            return {FAJITA: dict(FAJITA_LOW)}
        seen["grams_hint"] = grams_hint
        return {FAJITA: FAJITA_FIXED}
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", fake_llm)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Fajitalar"][0]
    assert item["macros"]["calories"] == 430        # yeniden tahmin uygulandı
    assert seen["grams_hint"] == {FAJITA: 220.0}     # gramaj otorite ipucu olarak geçti


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_keeps_original_when_reestimate_not_better(client, profile_session, monkeypatch):
    # Yeniden tahmin hâlâ imkânsız-düşükse özgün değer korunur (asla kötüleştirme).
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **kw: {"Fajitalar": [FAJITA]})
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", _forbidden_fatsecret)
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", _forbidden_fatsecret)
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm",
                        lambda items, category_map=None, grams_hint=None:
                        {FAJITA: dict(FAJITA_LOW)} if grams_hint is None else
                        {FAJITA: {"calories": 90.0, "protein": 10, "carbs": 6, "fat": 1}})

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    item = body["categories"]["Fajitalar"][0]
    assert item["macros"]["calories"] == 125        # özgün değer korundu


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_does_not_reestimate_dense_stated_gram_item(client, profile_session, monkeypatch):
    # Gerçekçi yoğunluktaki gramajlı öğe (160g burger, 736 kcal) yeniden tahmin EDİLMEZ.
    burger = "BBQ & Cheddar Burger (160 GR)"
    monkeypatch.setattr(menu_analysis, "_extract_categorized_items",
                        lambda *a, **kw: {"Burgerler": [burger]})
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", _forbidden_fatsecret)
    monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", _forbidden_fatsecret)

    def boom(items, category_map=None, grams_hint=None):
        if grams_hint is None:  # first independent estimate
            return {burger: {"calories": 736.0, "protein": 42, "carbs": 34, "fat": 28}}
        raise AssertionError("yoğunluğu gerçekçi öğe yeniden tahmin edilmemeli")
    monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", boom)

    body = client.post("/api/menu/analyze", json={"menu_text": MENU_TEXT}).get_json()
    assert body["categories"]["Burgerler"][0]["macros"]["calories"] == 736


# "scale" removed: QR never requests FatSecret per-100g values (advice fallback);
# _mock_pipeline makes any FatSecret call raise, so that path is proven unreachable.
@pytest.mark.parametrize("mode", ["empty", "exception", "zero", "impossible", "low", "clamp", "band-low", "reestimate"])
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_analyze_response_content_logs_are_counts_only(client, profile_session, monkeypatch, caplog, mode):
    import logging

    secret = "https://restaurant.example/menu?signature=SYNTHETIC_ANALYZE_SECRET"
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    _mock_pipeline(monkeypatch, {secret: [secret]})
    monkeypatch.setattr(menu_analysis, "_get_cached_macros", lambda names, **kw: ({}, names))
    monkeypatch.setattr(menu_analysis, "_cache_macros", lambda *a, **kw: None)
    if mode == "empty":
        monkeypatch.setattr(menu_analysis, "_extract_categorized_items", lambda *a, **kw: {})
    elif mode == "exception":
        def fail(*a, **kw):
            raise RuntimeError(secret)
        monkeypatch.setattr(menu_analysis, "_extract_categorized_items", fail)
    elif mode in {"impossible", "low", "clamp", "band-low"}:
        calories = {"impossible": 9000, "low": 1, "clamp": 1200, "band-low": 200}[mode]
        macros = {"calories": calories, "protein": 20, "carbs": 20, "fat": 10}
        if mode == "clamp":
            macros.update(carbs=150, fat=58)
        monkeypatch.setattr(menu_analysis, "_estimate_macros_llm",
                            lambda *a, **kw: {} if kw.get("grams_hint") else {secret: dict(macros)})
        if mode in {"clamp", "band-low"}:
            monkeypatch.setattr(menu_analysis, "_primary_dish_type", lambda *a: "pizza")
    elif mode == "reestimate":
        monkeypatch.setattr(menu_analysis.nutrition_pipeline, "parse_stated_grams", lambda *a: 220)
        monkeypatch.setattr(menu_analysis, "_estimate_macros_llm", lambda *a, **kw: {secret: CHICKEN} if kw.get("grams_hint") else {})

    with caplog.at_level(logging.INFO):
        response = client.post("/api/menu/analyze", json={"menu_text": secret, "framework_state": secret})
    assert response.status_code == (422 if mode in {"empty", "exception"} else 200)
    expected = {
        "empty": "No food items extracted", "exception": "Extraction crashed: RuntimeError",
        "zero": "ZERO-MACRO ITEM", "impossible": "DISCARDED implausible item",
        "low": "DISCARDED implausibly-low dish", "clamp": "PORTION BAND CLAMP",
        "band-low": "PORTION BAND LOW",
        "reestimate": "Re-estimated: 1 item",
    }[mode]
    assert expected in caplog.text
    assert "SYNTHETIC_ANALYZE_SECRET" not in caplog.text
    assert "signature=" not in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize("mode", ["extract", "extract-nojson", "extract-badjson", "extract-error", "macro-nojson", "macro-badjson", "macro-error", "weights", "weights-error", "fatsecret-error"])
@pytest.mark.usefixtures("synthetic_ai_layer")
def test_real_menu_analysis_helpers_do_not_log_response_content(client, profile_session, monkeypatch, caplog, mode):
    import json
    import logging
    from app.services import ai_nutrition, fatsecret

    secret = "https://restaurant.example/menu?signature=SYNTHETIC_HELPER_SECRET"
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    monkeypatch.setattr(menu_analysis, "_get_cached_macros", lambda names, **kw: ({}, names))
    monkeypatch.setattr(menu_analysis, "_cache_macros", lambda *a, **kw: None)
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", lambda: "offline-token")
    # Keep the real extraction/weight/macro helpers; only provider boundaries are fake.
    def provider(**kw):
        if kw.get("feature") == "menu_extract":
            if mode == "extract-nojson":
                return secret
            if mode == "extract-badjson":
                return "{" + secret
            if mode == "extract-error":
                raise RuntimeError(secret)
            return json.dumps({"categories": {secret: [secret]}})
        if mode in {"macro-error", "weights-error"}:
            raise RuntimeError(secret)
        if mode == "macro-nojson":
            return secret
        if mode == "macro-badjson":
            return "{" + json.dumps(secret) + ": invalid}"
        if mode == "weights":
            return json.dumps({secret: secret})  # invalid grams triggers fallback log
        return json.dumps({secret: CHICKEN})
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", provider)
    if mode == "fatsecret-error":
        monkeypatch.setattr(ai_nutrition.ai_cache, "cache_get", lambda *a, **kw: None)
        def fail(*a, **kw):
            raise RuntimeError(secret)
        monkeypatch.setattr(ai_nutrition, "_openai_chat", fail)
        monkeypatch.setattr(fatsecret, "_fs_get", fail)
    else:
        monkeypatch.setattr(menu_analysis, "_lookup_macros_fatsecret", lambda *a, **kw: ({}, {secret: CHICKEN}) if mode.startswith("weights") else ({}, {}))
    with caplog.at_level(logging.DEBUG):
        if mode in {"weights", "weights-error", "fatsecret-error"}:
            # QR no longer reaches FatSecret or per-100g weight scaling (advice
            # fallback); these helpers stay live for other callers, so their log
            # hygiene is exercised directly with the same synthetic secret.
            with client.application.test_request_context():
                if mode == "fatsecret-error":
                    fatsecret._lookup_macros_fatsecret([secret], "offline-token")
                else:
                    ai_nutrition._estimate_serving_weights_llm(
                        [secret], fallback_weights={secret: 150.0}, return_fallbacks=True)
        else:
            response = client.post("/api/menu/analyze", json={"menu_text": secret, "headings": [secret]})
            assert response.status_code == (422 if mode.startswith("extract-") else 200)
    expected = {
        "extract": "LLM returned 1 categories", "extract-nojson": "No valid JSON braces",
        "extract-badjson": "salvage found nothing", "extract-error": "[EXTRACT] ERROR: RuntimeError",
        "macro-nojson": "LLM response has no JSON braces", "macro-badjson": "JSON repair failed: JSONDecodeError",
        "macro-error": "LLM BATCH ERROR: RuntimeError", "weights": "Serving weight fallback: 1 item",
        "weights-error": "LLM SERVING WEIGHT ERROR: RuntimeError", "fatsecret-error": "FatSecret search failed: RuntimeError",
    }[mode]
    assert expected in caplog.text
    assert "SYNTHETIC_HELPER_SECRET" not in caplog.text
    assert "signature=" not in caplog.text
    assert secret not in caplog.text
