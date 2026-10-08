"""LP15-C native menu analysis: `POST /api/v1/nutrition/menu/analyze`.

Ownership proofs use real opaque Bearer credentials (`mobile_auth.login`; only
Cognito is faked). Acquisition runs through the real LP15-B1 boundary
(`menu_remote`): either the offline routed wire (real URL/DNS/redirect/media/
budget admission, only socket I/O faked) or, for literal private addresses,
the real credential-free subprocess worker with nothing patched. The model
provider is faked only at `ai_nutrition._heavy_chat`; FatSecret is offline.
"""
import calendar
import json
import logging
from datetime import datetime, timedelta

import pytest
import requests

from app.blueprints import mobile_menu as mobile_menu_bp
from app.extensions import db, limiter
from app.models import CustomMeal, CustomMealItem, MealLog, NutritionPlan, UserSession
from app.services import (
    ai_gate, ai_nutrition, cognito_jwt, cognito_service, foodcache,
    menu_analysis, menu_ocr, menu_remote as mr, mobile_auth, mobile_menu,
)
from app.timeutil import day_key

from menu_wire_support import PUBLIC, routed_wire  # noqa: F401

PATH = "/api/v1/nutrition/menu/analyze"
ENVELOPE_KEYS = {"code", "message", "retryable", "request_id"}

MENU_URL = "https://public.example/menu"
SUB_URL = "https://public.example/menu/tatlilar"

MAIN_HTML = """<html><head><title>Lezzet Duragi</title></head><body>
<a href="https://public.example/menu/tatlilar">Tatlilar</a>
<h2>Ana Yemekler</h2><ul><li>Izgara Tavuk</li><li>Adana Kebap</li>
<li>Gizemli Tabak</li></ul>
<p>SYNTHETIC_BODY_ONLY_PARAGRAPH Mercimek Corbasi</p>
<script>window.__NUXT__={"menu":{"note":"SYNTHETIC_FRAMEWORK_STATE"}}</script>
</body></html>"""
SUB_HTML = "<html><body><h2>Tatlilar</h2><ul><li>Kunefe</li></ul></body></html>"

CATEGORIES = {"Ana Yemekler": ["Izgara Tavuk", "Adana Kebap", "Gizemli Tabak"],
              "Tatlilar": ["Kunefe"]}
MACROS = {
    "Izgara Tavuk": {"calories": 330, "protein": 62, "carbs": 0, "fat": 7},
    "Adana Kebap": {"calories": 650, "protein": 45, "carbs": 10, "fat": 48},
    "Kunefe": {"calories": 540, "protein": 11, "carbs": 72, "fat": 25},
}


# -- Principals -------------------------------------------------------------------
@pytest.fixture
def bearer(monkeypatch):
    """Issue a real opaque mobile session for a user; only Cognito is faked."""
    subs = {}

    def authenticate(username, password):
        sub = subs[username]
        return {"tokens": {
            "access_token": f"access|{sub}", "id_token": f"id|{sub}",
            "refresh_token": f"refresh|{sub}", "expires_in": 3600},
            "claims": {"sub": sub}}

    def validate(token, expected_use, leeway_seconds=0):
        sub = token.split("|", 1)[1]
        if expected_use == "id":
            return {"sub": sub, "email": f"{sub}@example.com",
                    "email_verified": True}
        return {"sub": sub, "exp": calendar.timegm(
            (datetime.utcnow() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)

    def issue(user):
        subs[user.username] = user.cognito_sub
        issued = mobile_auth.login(user.username, "Sifre123")
        return {"Authorization": f"Bearer {issued.access_credential}"}
    return issue


def _with_target(user, calories, goal):
    db.session.add(UserSession(user_id=user.id, target_calories=calories, goal=goal))
    db.session.commit()
    return user


@pytest.fixture
def alice(make_user):
    return _with_target(make_user("alice"), 2000, "kas kazanma")


@pytest.fixture
def bob(make_user):
    user = _with_target(make_user("bob"), 1500, "kilo verme")
    db.session.add(MealLog(user_id=user.id, ogun="Kahvaltı", yemekler="x",
                           kalori=400, protein=20, karb=40, yag=10, tarih=day_key()))
    db.session.commit()
    return user


@pytest.fixture
def mobile(app):
    return app.test_client()


# -- Provider + wire -----------------------------------------------------------------
class Provider:
    def __init__(self):
        self.prompts = []
        self.categories = CATEGORIES
        self.fail = None

    def __call__(self, messages, system_prompt=None, temperature=None,
                 max_tokens=None, feature=None, **kwargs):
        self.prompts.append((feature, messages[0]["content"]))
        if self.fail:
            raise self.fail
        if feature == "menu_extract":
            return json.dumps({"categories": self.categories})
        names = [n for n in MACROS if n in messages[0]["content"]]
        return json.dumps({n: MACROS[n] for n in names})


@pytest.fixture
def provider(monkeypatch):
    fake = Provider()
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", fake)

    def offline():
        raise RuntimeError("fatsecret offline in tests")
    monkeypatch.setattr(menu_analysis, "_get_fatsecret_token", offline)
    monkeypatch.setattr(menu_analysis, "redis_client", None)
    foodcache._macro_cache.clear()
    yield fake
    foodcache._macro_cache.clear()


@pytest.fixture
def wire(routed_wire):
    routed_wire.host("public.example", PUBLIC)
    routed_wire.route(MENU_URL, MAIN_HTML)
    routed_wire.route(SUB_URL, SUB_HTML)
    return routed_wire


def analyze(client, headers, body=None, **kwargs):
    return client.post(PATH, headers=headers,
                       json={"url": MENU_URL} if body is None else body, **kwargs)


def _error(response):
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == ENVELOPE_KEYS
    assert not body["error"]["code"].startswith("AUTH_")
    return body["error"]


def _nutrition_rows():
    return (MealLog.query.count(), NutritionPlan.query.count(),
            CustomMeal.query.count(), CustomMealItem.query.count())


# == 1. Bearer required ===========================================================
def test_bearer_is_required_and_nothing_is_fetched(mobile, wire, provider):
    response = mobile.post(PATH, json={"url": MENU_URL})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert wire.seen == [] and provider.prompts == []

    garbage = mobile.post(PATH, json={"url": MENU_URL},
                          headers={"Authorization": "Bearer not-a-credential"})
    assert garbage.status_code == 401
    assert wire.seen == []


def test_browser_session_does_not_authorize_the_native_route(
        client, auth_user, wire, provider):
    response = client.post(PATH, json={"url": MENU_URL})
    assert response.status_code == 401
    assert wire.seen == []


# == 2. Cross-account identity ==================================================
@pytest.mark.parametrize("extra", [
    {"user_id": 2}, {"account": "bob"}, {"username": "bob"}, {"sub": "sub-bob"},
    {"menu_text": "Izgara Tavuk"}, {"body_text": "x"}, {"framework_state": "{}"},
    {"headings": ["x"]}, {"macros": {"calories": 1}}, {"items": []},
    {"menu_source": "google_drive"},
])
def test_any_field_beyond_url_is_refused_before_network(
        mobile, bearer, alice, wire, provider, extra):
    response = analyze(mobile, bearer(alice), {"url": MENU_URL, **extra})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "INVALID_MENU_REQUEST"
    assert error["retryable"] is False
    assert wire.seen == [] and provider.prompts == []


@pytest.mark.parametrize("body", [None, [], "https://public.example/menu", {},
                                  {"url": None}, {"url": 7}, {"url": ["x"]}])
def test_malformed_bodies_are_refused(mobile, bearer, alice, wire, provider, body):
    response = mobile.post(PATH, headers=bearer(alice),
                           data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_MENU_REQUEST"
    assert wire.seen == []


def test_owner_comes_only_from_the_bearer_credential(
        mobile, bearer, alice, bob, wire, provider):
    a = analyze(mobile, bearer(alice), query_string={"user_id": bob.id}).get_json()
    b = analyze(mobile, bearer(bob)).get_json()

    # Alice: 2000 kcal muscle-gain target, nothing eaten.
    assert a["menu_analysis"]["day"]["target"]["energy_kcal"] == 2000
    assert a["menu_analysis"]["day"]["consumed"]["energy_kcal"] == 0
    # Bob: 1500 kcal, 400 eaten — and only Bob's request sees it.
    assert b["menu_analysis"]["day"]["target"]["energy_kcal"] == 1500
    assert b["menu_analysis"]["day"]["remaining"]["energy_kcal"] == 1100
    # Item references are owner-bound: same menu, different accounts.
    ids_a = {i["item_id"] for c in a["menu_analysis"]["categories"] for i in c["items"]}
    ids_b = {i["item_id"] for c in b["menu_analysis"]["categories"] for i in c["items"]}
    assert ids_a and ids_b and not ids_a & ids_b


# == 3/8. HTTPS-only intake, userinfo, malformed ================================
@pytest.mark.parametrize("url", [
    "http://public.example/menu", "HTTP://public.example/menu",
    "http://user:pw@public.example/menu",
])
def test_plain_http_is_refused_before_the_shared_fetcher(
        mobile, bearer, alice, wire, provider, monkeypatch, url):
    monkeypatch.setattr(mr, "run_worker", lambda *a, **k: pytest.fail("fetch reached"))
    response = analyze(mobile, bearer(alice), {"url": url})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "MENU_HTTPS_REQUIRED"
    assert error["retryable"] is False
    assert wire.seen == []


@pytest.mark.parametrize("url", [
    "https://user@public.example/menu",
    "https://user:pw@public.example/menu",
    "https://public.example@evil.example/menu",
    "https://:pw@public.example/",
    "ftp://public.example/menu", "file:///etc/passwd", "javascript:alert(1)",
    "public.example/menu", "//public.example/menu", "https://", "https:///menu",
    "https://public.example:8443/menu", "https://pub lic.example/",
    "https://public.example/\\menu", "https://public.example/\x00",
    "https://[fe80::1%25eth0]/", "", "   ",
    "https://public.example/" + "a" * 2100,
])
def test_userinfo_and_malformed_urls_are_refused(
        mobile, bearer, alice, wire, provider, monkeypatch, url):
    monkeypatch.setattr(mr, "run_worker", lambda *a, **k: pytest.fail("fetch reached"))
    response = analyze(mobile, bearer(alice), {"url": url})
    assert response.status_code == 400
    assert _error(response)["code"] == "INVALID_MENU_URL"
    assert wire.seen == []


# == 4. HTTPS public URL admitted through the real boundary =====================
def test_public_https_menu_is_analyzed_through_the_canonical_boundary(
        mobile, bearer, alice, wire, provider):
    before = _nutrition_rows()
    response = analyze(mobile, bearer(alice),
                       {"url": "  https://public.example/menu?utm_source=qr#top "})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    # The canonical fetcher ran: tracking param + fragment cleaned, sub-page
    # crawled, exactly the LP15-B1 credential-free headers on every hop.
    assert wire.urls() == [MENU_URL, SUB_URL]
    assert all(dict(r.headers) == mr.OUTBOUND_HEADERS for r, _ in wire.seen)
    assert all(kw.get("proxies") == {} and kw.get("verify") is True
               for _, kw in wire.seen)
    # Server-side scan → analysis: the model saw the fetched text.
    extract_prompts = [p for f, p in provider.prompts if f == "menu_extract"]
    assert len(extract_prompts) == 1 and "Kunefe" in extract_prompts[0]

    body = response.get_json()["menu_analysis"]
    assert body["contract_version"] == 1
    assert body["source"] == {"kind": "web_page", "host": "public.example",
                              "title": "Lezzet Duragi"}
    assert [c["name"] for c in body["categories"]] == ["Ana Yemekler", "Tatlilar"]
    assert body["item_count"] == 4
    assert _nutrition_rows() == before


def test_dto_shape_is_bounded_and_unknown_nutrition_stays_null(
        mobile, bearer, alice, wire, provider):
    body = analyze(mobile, bearer(alice)).get_json()
    assert set(body) == {"menu_analysis"}
    menu = body["menu_analysis"]
    assert set(menu) == {"contract_version", "source", "day", "categories",
                         "coach_pick_ids", "item_count"}
    assert set(menu["day"]) == {"target", "consumed", "remaining"}
    for macros in menu["day"].values():
        assert set(macros) == {"energy_kcal", "protein_g", "carbohydrate_g", "fat_g"}

    items = {i["name"]: i for c in menu["categories"] for i in c["items"]}
    for item in items.values():
        assert set(item) == {"item_id", "name", "nutrition", "estimate", "fit"}
        assert set(item["nutrition"]) == {"status", "energy_kcal", "protein_g",
                                          "carbohydrate_g", "fat_g"}
        assert set(item["fit"]) == {"score", "flags", "warnings"}

    chicken = items["Izgara Tavuk"]
    assert chicken["nutrition"] == {"status": "estimated", "energy_kcal": 330,
                                    "protein_g": 62, "carbohydrate_g": 0, "fat_g": 7}
    assert chicken["estimate"] == {"source": "llm", "confidence": 0.6}
    assert chicken["fit"]["score"] == 100
    assert set(chicken["fit"]["flags"]) <= mobile_menu.FIT_FLAGS
    assert "high_protein" in chicken["fit"]["flags"]
    # Canonical warning, as a locale-free token (never the Turkish web text).
    assert items["Adana Kebap"]["fit"]["warnings"] == ["approaching_fat_budget"]

    # No provider/estimate produced numbers: unknown, never 0.
    mystery = items["Gizemli Tabak"]
    assert mystery["nutrition"] == {"status": "unknown", "energy_kcal": None,
                                    "protein_g": None, "carbohydrate_g": None,
                                    "fat_g": None}
    assert mystery["estimate"] == {"source": None, "confidence": None}
    assert mystery["fit"] == {"score": None, "flags": [], "warnings": []}

    ids = [i["item_id"] for i in items.values()]
    assert len(set(ids)) == len(ids)
    assert set(menu["coach_pick_ids"]) <= set(ids)
    assert items["Gizemli Tabak"]["item_id"] not in menu["coach_pick_ids"]


def test_dish_and_category_names_are_bounded_not_rewritten(
        mobile, bearer, alice, wire, provider):
    long_name = "Çıtır Tavuk " + "ş" * 400
    provider.categories = {"Özel " + "Ç" * 300: [long_name, "Izgara Tavuk", "Izgara Tavuk"]}
    menu = analyze(mobile, bearer(alice)).get_json()["menu_analysis"]
    [category] = menu["categories"]
    assert category["name"] == ("Özel " + "Ç" * 300)[:mobile_menu.MAX_CATEGORY_CHARS]
    names = [i["name"] for i in category["items"]]
    assert long_name[:mobile_menu.MAX_NAME_CHARS] in names
    assert names.count("Izgara Tavuk") == 1  # repeated dish → one item, one id


# == 5. Private / special destinations ===========================================
@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.7"], ["172.16.4.4"], ["192.168.1.1"],
    ["169.254.169.254"], ["100.64.0.1"], ["0.0.0.0"], ["224.0.0.1"],
    ["::1"], ["fd00::1"], ["fe80::1"], ["::ffff:127.0.0.1"], ["64:ff9b::a00:1"],
    [PUBLIC, "10.0.0.1"],
])
def test_private_and_special_dns_answers_are_blocked(
        mobile, bearer, alice, wire, provider, addresses):
    wire.host("internal.example", *addresses)
    response = analyze(mobile, bearer(alice), {"url": "https://internal.example/menu"})
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "MENU_DESTINATION_BLOCKED"
    assert error["retryable"] is False
    assert wire.seen == [] and provider.prompts == []


@pytest.mark.parametrize("url", [
    "https://127.0.0.1/menu", "https://169.254.169.254/latest/meta-data/",
    "https://[::1]/menu", "https://localhost/menu", "https://10.1.2.3/",
])
def test_literal_private_destinations_blocked_by_the_real_worker(
        mobile, bearer, alice, provider, url):
    # No wire, nothing patched in menu_remote: the real credential-free
    # subprocess worker resolves and refuses before any connection.
    response = analyze(mobile, bearer(alice), {"url": url})
    assert response.status_code == 422
    assert _error(response)["code"] == "MENU_DESTINATION_BLOCKED"
    assert provider.prompts == []


# == 6/7. Redirect admission =====================================================
def test_redirect_to_private_destination_is_blocked(
        mobile, bearer, alice, wire, provider):
    wire.host("internal.example", "10.0.0.7")
    wire.redirect(MENU_URL, "https://internal.example/admin")
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    assert _error(response)["code"] == "MENU_DESTINATION_BLOCKED"
    assert wire.urls() == [MENU_URL]
    assert provider.prompts == []


@pytest.mark.parametrize("location", [
    "http://public.example/menu", "http://other.example/menu",
    "HTTP://public.example:80/x",
])
def test_https_to_http_redirect_is_refused(
        mobile, bearer, alice, wire, provider, location):
    wire.host("other.example", PUBLIC)
    wire.redirect(MENU_URL, location)
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "MENU_HTTPS_REQUIRED"
    assert error["retryable"] is False
    assert wire.urls() == [MENU_URL]


def test_redirect_to_userinfo_target_is_refused_before_next_hop(
        mobile, bearer, alice, wire, provider):
    wire.redirect(MENU_URL, "https://user:pw@public.example/next")
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    assert _error(response)["code"] == "MENU_DESTINATION_BLOCKED"
    assert wire.urls() == [MENU_URL]


def test_https_redirect_chain_is_followed_and_capped(
        mobile, bearer, alice, wire, provider):
    wire.redirect(MENU_URL, "https://public.example/menu2")
    wire.route("https://public.example/menu2", MAIN_HTML)
    assert analyze(mobile, bearer(alice)).status_code == 200

    for i in range(7):
        wire.redirect(f"https://public.example/loop{i}", f"https://public.example/loop{i+1}")
    response = analyze(mobile, bearer(alice), {"url": "https://public.example/loop0"})
    assert response.status_code == 502
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_FETCH_FAILED", False)


def _http_sub_link_page(wire):
    html = MAIN_HTML.replace("https://public.example/menu/tatlilar",
                             "http://public.example/menu/tatlilar")
    wire.route(MENU_URL, html)
    wire.route("http://public.example/menu/tatlilar", SUB_HTML)


def test_http_sub_pages_are_never_followed_by_native_intake(
        mobile, bearer, alice, wire, provider):
    _http_sub_link_page(wire)
    assert analyze(mobile, bearer(alice)).status_code == 200
    assert wire.urls() == [MENU_URL]


def test_web_scan_keeps_following_http_sub_pages(client, auth_user, wire, provider):
    # The web contract (HTTP compatibility, LP15-B1) is unchanged.
    _http_sub_link_page(wire)
    assert client.post("/api/proxy/scan-menu", json={"url": MENU_URL}).status_code == 200
    assert wire.urls() == [MENU_URL, "http://public.example/menu/tatlilar"]


# == 9. Unsupported media ========================================================
@pytest.mark.parametrize("headers,body", [
    ({"Content-Type": "application/pdf"}, b"%PDF-1.7"),
    ({"Content-Type": "text/html"}, b"  %PDF-1.4 disguised"),
    ({"Content-Type": "image/png"}, b"\x89PNG\r\n"),
    ({"Content-Type": "image/jpeg"}, b"\xff\xd8\xff"),
    ({"Content-Type": "application/octet-stream"}, b"x"),
    ({"Content-Type": "application/json"}, b"{}"),
    ({"Content-Type": "text/html", "Content-Encoding": "gzip"}, b"\x1f\x8b"),
])
def test_unsupported_media_fails_closed_without_parsers(
        mobile, bearer, alice, wire, provider, monkeypatch, headers, body):
    monkeypatch.setattr(menu_ocr, "_extract_text_from_pdf", lambda *a: pytest.fail("PDF parser"))
    monkeypatch.setattr(menu_ocr, "_extract_text_from_image", lambda *a: pytest.fail("image parser"))
    wire.route(MENU_URL, body, headers=headers)
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_UNSUPPORTED_MEDIA", False)
    assert provider.prompts == []


def test_drive_document_uses_the_same_boundary_and_pdf_stays_disabled(
        mobile, bearer, alice, routed_wire, provider):
    routed_wire.host("docs.google.com", PUBLIC)
    routed_wire.host("drive.google.com", PUBLIC)
    routed_wire.route("https://docs.google.com/document/d/DOC1/export?format=txt",
                      "Ana Yemekler\nIzgara Tavuk\nAdana Kebap\nTatlilar\nKunefe",
                      headers={"Content-Type": "text/plain"})
    ok = analyze(mobile, bearer(alice), {"url": "https://docs.google.com/document/d/DOC1/edit"})
    assert ok.status_code == 200
    assert ok.get_json()["menu_analysis"]["source"] == {
        "kind": "google_drive", "host": "docs.google.com", "title": None}

    routed_wire.route("https://drive.google.com/uc?export=download&id=F3", b"%PDF-1.7",
                      headers={"Content-Type": "application/pdf"})
    pdf = analyze(mobile, bearer(alice), {"url": "https://drive.google.com/file/d/F3/view"})
    assert pdf.status_code == 422
    assert _error(pdf)["code"] == "MENU_UNSUPPORTED_MEDIA"


# == 10. Size / parser bounds =====================================================
@pytest.mark.parametrize("body,headers", [
    (b"<p>" + b"a" * (mr.MAX_BYTES + 1), {}),
    (b"<p>x</p>", {"Content-Length": str(mr.MAX_BYTES + 1)}),
    ("<div>" * 80 + "Kebap" + "</div>" * 80, {}),
    ("<p>" + "<b>x</b>" * 7000 + "</p>", {}),
])
def test_oversized_or_parser_bound_input_fails_closed(
        mobile, bearer, alice, wire, provider, body, headers):
    wire.route(MENU_URL, body, headers=headers)
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_CONTENT_TOO_LARGE", False)
    assert provider.prompts == []


def test_unreadable_page_is_a_typed_parse_failure(
        mobile, bearer, alice, wire, provider):
    wire.route(MENU_URL, "<html><body><p>hi</p></body></html>")
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    assert _error(response)["code"] == "MENU_PARSE_FAILED"
    assert provider.prompts == []


def test_nothing_extracted_is_a_typed_parse_failure(
        mobile, bearer, alice, wire, provider):
    provider.categories = {}
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 422
    assert _error(response)["code"] == "MENU_PARSE_FAILED"


# == Upstream failures ===========================================================
def test_timeouts_and_connection_failures_are_typed_and_retryable(
        mobile, bearer, alice, wire, provider, monkeypatch):
    headers = bearer(alice)

    def timeout(payload, seconds):
        raise requests.Timeout("MENU_FETCH_TIMEOUT")
    monkeypatch.setattr(mr, "run_worker", timeout)
    response = analyze(mobile, headers)
    assert response.status_code == 504
    assert (_error(response)["code"], _error(response)["retryable"]) == ("MENU_FETCH_TIMEOUT", True)

    def failed(payload, seconds):
        raise requests.ConnectionError("MENU_FETCH_FAILED")
    monkeypatch.setattr(mr, "run_worker", failed)
    response = analyze(mobile, headers)
    assert response.status_code == 502
    assert (_error(response)["code"], _error(response)["retryable"]) == ("MENU_FETCH_FAILED", True)


def test_upstream_client_error_is_not_retryable(mobile, bearer, alice, wire, provider):
    wire.route(MENU_URL, "gone", status=404)
    response = analyze(mobile, bearer(alice))
    assert response.status_code == 502
    assert (_error(response)["code"], _error(response)["retryable"]) == ("MENU_FETCH_FAILED", False)


def test_unknown_host_is_a_fetch_failure(mobile, bearer, alice, wire, provider):
    response = analyze(mobile, bearer(alice), {"url": "https://nowhere.example/menu"})
    assert response.status_code == 502
    assert _error(response)["code"] == "MENU_FETCH_FAILED"


def test_profile_without_target_is_a_typed_conflict(
        mobile, bearer, make_user, wire, provider):
    user = make_user("notarget")
    response = analyze(mobile, bearer(user))
    assert response.status_code == 409
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_PROFILE_INCOMPLETE", False)


# == 11. Log hygiene ==============================================================
SECRET_PAGE = """<html><head><title>SYNTHETIC_TITLE_SECRET</title></head><body>
<a href="https://public.example/menu/SYNTHETIC_LINK_SECRET?token=SYNTHETIC_LINK_TOKEN">menu</a>
<h2>SYNTHETIC_HEADING_SECRET</h2><ul><li>Izgara Tavuk</li><li>Adana Kebap</li></ul>
<p>SYNTHETIC_BODY_SECRET</p></body></html>"""


@pytest.mark.parametrize("mode", ["success", "provider-error", "fetch-blocked",
                                  "sub-page-error", "unexpected"])
def test_logs_carry_no_remote_content_url_secret_or_identity(
        mobile, bearer, alice, wire, provider, caplog, monkeypatch, mode):
    url = "https://public.example/menu/private-path?signature=SYNTHETIC_QUERY_SECRET"
    clean = "https://public.example/menu/private-path?signature=SYNTHETIC_QUERY_SECRET"
    wire.route(clean, SECRET_PAGE)
    provider.categories = {"SYNTHETIC_CATEGORY_SECRET": ["SYNTHETIC_DISH_SECRET Kebap"]}
    if mode == "provider-error":
        provider.fail = RuntimeError("SYNTHETIC_PROVIDER_SECRET")
    elif mode == "fetch-blocked":
        wire.host("public.example", "10.0.0.9")
    elif mode == "unexpected":
        def explode(*a, **k):
            raise RuntimeError("SYNTHETIC_EXCEPTION_SECRET")
        monkeypatch.setattr(menu_analysis, "analyze_menu_text", explode)
    headers = bearer(alice)

    with caplog.at_level(logging.DEBUG):
        response = analyze(mobile, headers, {"url": url})

    assert response.status_code in {200, 422, 503}
    text = caplog.text
    for secret in ("SYNTHETIC_QUERY_SECRET", "signature=", "private-path",
                   "SYNTHETIC_TITLE_SECRET", "SYNTHETIC_HEADING_SECRET",
                   "SYNTHETIC_BODY_SECRET", "SYNTHETIC_LINK_SECRET",
                   "SYNTHETIC_LINK_TOKEN", "SYNTHETIC_CATEGORY_SECRET",
                   "SYNTHETIC_DISH_SECRET", "SYNTHETIC_PROVIDER_SECRET",
                   "SYNTHETIC_EXCEPTION_SECRET", "Izgara Tavuk", "Adana Kebap",
                   headers["Authorization"].split()[1], alice.cognito_sub,
                   alice.email):
        assert secret not in text, secret
    assert f"user_id={alice.id}" not in text and f"user={alice.id}" not in text
    assert "mobile_menu event=" in text


# == 12. Raw remote body never returned ===========================================
def test_raw_remote_body_and_internal_fields_never_reach_the_client(
        mobile, bearer, alice, wire, provider):
    raw = analyze(mobile, bearer(alice)).get_data(as_text=True)
    for leaked in ("SYNTHETIC_BODY_ONLY_PARAGRAPH", "SYNTHETIC_FRAMEWORK_STATE",
                   "body_text", "framework_state", "headings", "source_url",
                   "crawl_errors", "menu_text", "/menu/tatlilar", "macro_source",
                   "reason", "Yüksek protein", "Günlük bütçe"):
        assert leaked not in raw, leaked


# == 13. Zero writes ===============================================================
def test_analysis_writes_no_diary_plan_or_custom_meal_rows(
        app, mobile, bearer, alice, bob, wire, provider):
    from sqlalchemy import event

    headers = bearer(alice)
    before = _nutrition_rows()
    writes = []

    def record(conn, cursor, statement, params, context, executemany):
        head = statement.lstrip().split(None, 1)[0].upper()
        if head in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        ok = analyze(mobile, headers)
        provider.categories = {}
        failed = analyze(mobile, headers)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert (ok.status_code, failed.status_code) == (200, 422)
    assert _nutrition_rows() == before
    nutrition_tables = ("meal_log", "nutrition_plan", "custom_meal", "custom_meal_item")
    assert not [w for w in writes if any(t in w.lower() for t in nutrition_tables)]


# == 14. Failures never invalidate a valid session =================================
@pytest.mark.parametrize("failure", ["provider", "fetch", "storage", "acquisition-bug"])
def test_menu_failures_are_not_auth_failures_and_keep_the_session(
        mobile, bearer, alice, wire, provider, monkeypatch, failure):
    headers = bearer(alice)
    with pytest.MonkeyPatch.context() as mp:
        if failure == "provider":
            def boom(*a, **k):
                raise RuntimeError("model down")
            mp.setattr(menu_analysis, "_extract_categorized_items", boom)
            expected = (422, "MENU_PARSE_FAILED", False)
        elif failure == "fetch":
            def refused(payload, seconds):
                raise requests.ConnectionError("x")
            mp.setattr(mr, "run_worker", refused)
            expected = (502, "MENU_FETCH_FAILED", True)
        elif failure == "storage":
            class Broken:
                def filter_by(self, **kw):
                    raise RuntimeError("database unavailable")
            mp.setattr(menu_analysis.MealLog, "query", Broken())
            expected = (503, "MENU_ANALYSIS_FAILED", True)
        else:
            def bug(*a, **k):
                raise KeyError("unexpected")
            mp.setattr(menu_analysis, "_discover_menu_links", bug)
            expected = (503, "MENU_ANALYSIS_FAILED", True)

        response = analyze(mobile, headers)
    error = _error(response)
    assert (response.status_code, error["code"], error["retryable"]) == expected
    # The same Bearer credential still works.
    me = mobile.get("/api/v1/account/me", headers=headers)
    assert me.status_code == 200
    assert me.get_json()["user"]["username"] == "alice"


# == Capacity, phases, limits ======================================================
def test_scrape_and_ai_phases_never_hold_both_permits(
        mobile, bearer, alice, wire, provider, monkeypatch):
    seen = {"fetch": [], "model": []}
    send = wire.send

    def observed_send(adapter, request, **kwargs):
        seen["fetch"].append(ai_gate.capacity_snapshot())
        return send(adapter, request, **kwargs)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        lambda adapter, request, **kw: observed_send(adapter, request, **kw))

    def observed_provider(*args, **kwargs):
        seen["model"].append(ai_gate.capacity_snapshot())
        return provider(*args, **kwargs)
    monkeypatch.setattr(ai_nutrition, "_heavy_chat", observed_provider)

    headers = bearer(alice)
    assert analyze(mobile, headers).status_code == 200
    assert seen["fetch"] and seen["model"]
    assert all(s["scrape_active"] == 1 and s["ai_active"] == 0 for s in seen["fetch"])
    assert all(s["scrape_active"] == 0 and s["ai_active"] == 1 for s in seen["model"])
    snapshot = ai_gate.capacity_snapshot()
    assert (snapshot["scrape_active"], snapshot["ai_active"]) == (0, 0)


class _Full:
    def acquire(self, timeout=None, blocking=True):
        return False

    def release(self):
        raise AssertionError("released a permit that was never acquired")


def test_busy_scrape_gate_is_typed_and_does_no_network(
        mobile, bearer, alice, wire, provider, monkeypatch):
    headers = bearer(alice)
    monkeypatch.setattr(ai_gate, "_scrape_slots", _Full())
    response = analyze(mobile, headers)
    assert response.status_code == 503
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_ANALYSIS_BUSY", True)
    assert response.headers["Retry-After"] == "15"
    assert wire.seen == [] and provider.prompts == []


def test_busy_ai_gate_is_typed_and_reaches_no_model(
        mobile, bearer, alice, wire, provider, monkeypatch):
    headers = bearer(alice)  # login itself takes a blocking slot
    monkeypatch.setattr(ai_gate, "_ai_slots", _Full())
    response = analyze(mobile, headers)
    assert response.status_code == 503
    assert _error(response)["code"] == "MENU_ANALYSIS_BUSY"
    assert response.headers["Retry-After"] == "15"
    assert provider.prompts == []
    assert ai_gate.capacity_snapshot()["scrape_active"] == 0


@pytest.fixture
def enabled_limiter():
    limiter.reset()
    limiter.enabled = True
    try:
        yield
    finally:
        limiter.enabled = False
        limiter.reset()


@pytest.mark.parametrize("name", ["SCRAPE_RATELIMIT", "AI_RATELIMIT", "BEDROCK_RATELIMIT"])
def test_rate_limits_are_typed_owner_keyed_and_checked_before_work(
        mobile, bearer, alice, bob, wire, provider, enabled_limiter, monkeypatch, name):
    monkeypatch.setattr(mobile_menu_bp, name, "1 per hour")
    a = bearer(alice)
    assert analyze(mobile, a).status_code == 200
    fetched, prompted = len(wire.seen), len(provider.prompts)

    response = analyze(mobile, a)
    assert response.status_code == 429
    error = _error(response)
    assert (error["code"], error["retryable"]) == ("MENU_ANALYSIS_RATE_LIMITED", True)
    assert int(response.headers["Retry-After"]) > 0
    assert (len(wire.seen), len(provider.prompts)) == (fetched, prompted)
    # Keyed on the Bearer owner: Bob is not throttled by Alice.
    assert analyze(mobile, bearer(bob)).status_code == 200


def test_invalid_intake_does_not_consume_rate_limit(
        mobile, bearer, alice, wire, provider, enabled_limiter, monkeypatch):
    monkeypatch.setattr(mobile_menu_bp, "BEDROCK_RATELIMIT", "1 per hour")
    a = bearer(alice)
    for _ in range(3):
        assert analyze(mobile, a, {"url": "http://public.example/menu"}).status_code == 400
    assert analyze(mobile, a).status_code == 200


# == Caches ==========================================================================
class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


def test_native_scan_cache_is_separate_from_the_web_namespace(
        app, mobile, bearer, alice, wire, provider, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(menu_analysis, "redis_client", fake)
    # A web scan of the same HTTPS URL may contain HTTP sub-pages; native must
    # never be served that entry.
    with app.test_request_context():
        menu_analysis.acquire_menu(MENU_URL)
    assert any(k.startswith(menu_analysis.MENU_SCAN_CACHE_PREFIX)
               and not k.startswith(menu_analysis.MENU_SCAN_HTTPS_ONLY_CACHE_PREFIX)
               for k in fake.store)
    web_fetches = len(wire.seen)
    headers = bearer(alice)
    assert analyze(mobile, headers).status_code == 200
    assert len(wire.seen) == web_fetches * 2
    assert any(k.startswith(menu_analysis.MENU_SCAN_HTTPS_ONLY_CACHE_PREFIX) for k in fake.store)
    # A repeat native analysis is served from its own namespace.
    assert analyze(mobile, headers).status_code == 200
    assert len(wire.seen) == web_fetches * 2


# == Architecture: one path, read-only ===============================================
def _tree(path):
    import ast
    from pathlib import Path
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _calls(tree):
    import ast
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _imported(tree):
    import ast
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def test_web_and_native_routes_are_thin_adapters_over_one_service():
    canonical = {"_fetch_page", "_process_google_drive_url", "_extract_categorized_items",
                 "_estimate_macros_llm", "_lookup_macros_fatsecret", "bounded_soup",
                 "derive_daily_macro_targets", "_menu_score", "fetch", "retrieve"}
    for path in ("app/blueprints/menu.py", "app/blueprints/mobile_menu.py",
                 "app/services/mobile_menu.py"):
        assert not canonical & _calls(_tree(path)), path
    native = _tree("app/blueprints/mobile_menu.py")
    assert {"acquire_menu", "analyze_menu_text"} <= _calls(native)
    assert not any("blueprints.menu" in name for name in _imported(native))
    web = _tree("app/blueprints/menu.py")
    assert {"acquire_menu", "analyze_menu_text", "web_analysis_payload"} <= _calls(web)


def test_menu_analysis_paths_never_write_the_database():
    import ast
    for path in ("app/services/menu_analysis.py", "app/services/mobile_menu.py",
                 "app/blueprints/mobile_menu.py"):
        tree = _tree(path)
        writes = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"add", "add_all", "commit", "flush", "merge",
                                           "bulk_save_objects", "execute", "delete"}):
                owner = node.func.value
                if ((isinstance(owner, ast.Attribute) and owner.attr == "session")
                        or (isinstance(owner, ast.Name) and owner.id in {"db", "session"})):
                    writes.append(node.func.attr)
        assert writes == [], (path, writes)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    assert not (isinstance(target, ast.Attribute)
                                and isinstance(target.value, ast.Name)
                                and target.value.id in {"m", "meal", "row", "sess"}), path
    native = _imported(_tree("app/services/mobile_menu.py"))
    assert not any(name.startswith(("app.models", "app.extensions", "flask"))
                   for name in native)


def test_item_ids_tolerate_any_decoded_model_string():
    lone = "Kebap \ud800"
    first = mobile_menu.item_id("secret", 1, "Ana", lone)
    assert first == mobile_menu.item_id("secret", 1, "Ana", lone)
    assert first != mobile_menu.item_id("secret", 2, "Ana", lone)
    assert first != mobile_menu.item_id("secret", 1, "Tatli", lone)
    assert len(first) == 24
