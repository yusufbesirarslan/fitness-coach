"""LP15-C characterization of the web menu endpoints.

`POST /api/proxy/scan-menu` and `POST /api/menu/analyze` must answer exactly as
they did before the canonical acquisition/analysis code moved into
`app.services.menu_analysis`. Each scenario's (status, JSON body) is compared
against `tests/fixtures/menu_web_golden.json`, recorded on the pre-refactor
base (origin/main 133c0ff).

The test patches each collaborator wherever it is bound (the blueprint before
the move, the service after it), and network acquisition runs through the real
`menu_remote` code on the offline routed wire — so the same test, unchanged,
characterizes both sides of the refactor.
"""
import importlib
import json
from pathlib import Path

import pytest

from app.blueprints import menu as menu_bp
from app.extensions import db
from app.models import MealLog, UserSession
from app.services import foodcache, menu_remote as mr

from menu_wire_support import PUBLIC, routed_wire  # noqa: F401

GOLDEN = Path(__file__).parent / "fixtures" / "menu_web_golden.json"

MAIN_HTML = """<html><head><title>Lezzet Duragi</title></head><body>
<a href="https://public.example/menu/tatlilar">Tatlilar</a>
<h2>Corbalar</h2><ul><li>Mercimek Corbasi</li><li>Ezogelin Corbasi</li></ul>
<h2>Ana Yemekler</h2><ul><li>Adana Kebap</li><li>Izgara Tavuk</li>
<li>Margherita Pizza</li><li>Ev Burger</li></ul>
<script>window.__NUXT__={"menu":{"items":["Kunefe"]}}</script>
</body></html>"""

SUB_HTML = """<html><body><h2>Tatlilar</h2><ul><li>Kunefe</li>
<li>Sutlac</li></ul></body></html>"""


def _owners():
    owners = [menu_bp]
    try:
        owners.append(importlib.import_module("app.services.menu_analysis"))
    except ImportError:
        pass
    return owners


def _patch(monkeypatch, name, value):
    patched = False
    for module in _owners():
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    assert patched, name


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    foodcache._macro_cache.clear()
    _patch(monkeypatch, "redis_client", None)
    yield
    foodcache._macro_cache.clear()


def _observe(response):
    return {"status": response.status_code, "body": response.get_json()}


# ---------------------------------------------------------------------------
# /api/proxy/scan-menu
# ---------------------------------------------------------------------------

def _scan(client, url):
    return _observe(client.post("/api/proxy/scan-menu", json={"url": url}))


def scan_scenarios(client, wire, monkeypatch):
    wire.host("public.example", PUBLIC)
    wire.host("private.example", "10.0.0.7")
    wire.host("drive.google.com", PUBLIC)
    wire.host("docs.google.com", PUBLIC)
    out = {}

    out["scan_missing_url"] = _observe(client.post("/api/proxy/scan-menu", json={}))
    out["scan_userinfo"] = _scan(client, "https://user:pw@public.example/menu")
    out["scan_bad_scheme"] = _scan(client, "ftp://public.example/menu")
    out["scan_private_dns"] = _scan(client, "https://private.example/menu")

    wire.route("https://public.example/menu?table=4", MAIN_HTML)
    wire.route("https://public.example/menu/tatlilar", SUB_HTML)
    out["scan_success_https"] = _scan(
        client, "https://public.example/menu?utm_source=qr&table=4#top")

    wire.route("http://public.example/plain", MAIN_HTML)
    out["scan_success_http"] = _scan(client, "http://public.example/plain")

    wire.redirect("https://public.example/r-private", "https://private.example/x")
    out["scan_redirect_private"] = _scan(client, "https://public.example/r-private")

    wire.redirect("https://public.example/r-down", "http://public.example/plain")
    out["scan_redirect_downgrade"] = _scan(client, "https://public.example/r-down")

    wire.route("https://public.example/pdf", b"%PDF-1.4", headers={"Content-Type": "application/pdf"})
    out["scan_pdf"] = _scan(client, "https://public.example/pdf")

    wire.route("https://public.example/img", b"\x89PNG", headers={"Content-Type": "image/png"})
    out["scan_image"] = _scan(client, "https://public.example/img")

    wire.route("https://public.example/big", b"<p>" + b"a" * (mr.MAX_BYTES + 10))
    out["scan_oversized"] = _scan(client, "https://public.example/big")

    deep = "<div>" * 80 + "Kebap" + "</div>" * 80
    wire.route("https://public.example/deep", deep)
    out["scan_parser_limit"] = _scan(client, "https://public.example/deep")

    wire.route("https://public.example/gone", "nope", status=404)
    out["scan_upstream_404"] = _scan(client, "https://public.example/gone")

    wire.route("https://public.example/empty", "<html><body></body></html>")
    out["scan_unreadable"] = _scan(client, "https://public.example/empty")

    wire.route("https://public.example/gz", "x", headers={"Content-Encoding": "gzip"})
    out["scan_encoding"] = _scan(client, "https://public.example/gz")

    out["scan_unknown_host"] = _scan(client, "https://nowhere.example/menu")

    wire.route("https://docs.google.com/document/d/DOC1/export?format=txt",
               "Corbalar\nMercimek Corbasi\nAna Yemekler\nAdana Kebap\nIzgara Tavuk",
               headers={"Content-Type": "text/plain"})
    out["scan_drive_doc"] = _scan(client, "https://docs.google.com/document/d/DOC1/edit")

    wire.route("https://drive.google.com/uc?export=download&id=F2", "denied", status=403)
    out["scan_drive_restricted"] = _scan(client, "https://drive.google.com/file/d/F2/view")

    wire.route("https://drive.google.com/uc?export=download&id=F3", b"%PDF-1.7",
               headers={"Content-Type": "application/pdf"})
    out["scan_drive_pdf"] = _scan(client, "https://drive.google.com/file/d/F3/view")

    def timeout(payload, seconds):
        import requests
        raise requests.Timeout("MENU_FETCH_TIMEOUT")
    monkeypatch.setattr(mr, "run_worker", timeout)
    out["scan_timeout"] = _scan(client, "https://public.example/menu")

    def failed(payload, seconds):
        import requests
        raise requests.ConnectionError("MENU_FETCH_FAILED")
    monkeypatch.setattr(mr, "run_worker", failed)
    out["scan_connection_failed"] = _scan(client, "https://public.example/menu")
    return out


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


def scan_cache_scenarios(client, wire, monkeypatch):
    wire.host("public.example", PUBLIC)
    wire.route("https://public.example/menu", MAIN_HTML)
    wire.route("https://public.example/menu/tatlilar", SUB_HTML)
    fake = FakeRedis()
    _patch(monkeypatch, "redis_client", fake)
    first = _scan(client, "https://public.example/menu")
    fetched = len(wire.seen)
    second = _scan(client, "HTTPS://Public.Example/menu/?utm_medium=x")
    assert len(wire.seen) == fetched  # served from the scan cache
    return {"scan_cache_store": first, "scan_cache_hit": second,
            "scan_cache_keys": sorted(k.rsplit(":", 1)[0] for k in fake.store)}


# ---------------------------------------------------------------------------
# /api/menu/analyze
# ---------------------------------------------------------------------------

CHICKEN = {"calories": 330.0, "protein": 62.0, "carbs": 0.0, "fat": 7.0}
MENU_TEXT = "Corbalar: Mercimek Corbasi. Ana Yemekler: Adana Kebap, Izgara Tavuk"


def _analyze(client, payload):
    return _observe(client.post("/api/menu/analyze", json=payload))


def analyze_scenarios(client, user, monkeypatch):
    out = {}
    out["analyze_short_text"] = _analyze(client, {"menu_text": "kisa"})
    out["analyze_no_profile"] = _analyze(client, {"menu_text": MENU_TEXT})

    db.session.add(UserSession(user_id=user.id, target_calories=2000, goal="kas kazanma"))
    from app.timeutil import day_key
    db.session.add(MealLog(user_id=user.id, ogun="Kahvaltı", yemekler="x",
                           kalori=500, protein=30, karb=50, yag=15, tarih=day_key()))
    db.session.commit()

    categorized = {
        "Ana Yemekler": ["Izgara Tavuk", "Adana Kebap", "Tavuklu Fajita (220 GR)",
                         "Margherita Pizza", "Imkansiz Tabak"],
        "Tatlilar": ["Kunefe", "Gizemli Tatli"],
        "Corbalar": ["Mercimek Corbasi"],
    }
    calls = []

    def extract(raw_text, fw_state=None, headings=None, menu_source=None):
        calls.append({"fw": fw_state, "headings": headings, "source": menu_source})
        return categorized if not fw_state else {}

    _patch(monkeypatch, "_extract_categorized_items", extract)
    _patch(monkeypatch, "_get_fatsecret_token", lambda: "tok")
    _patch(monkeypatch, "_lookup_macros_fatsecret", lambda names, tok, cmap=None: (
        {"Izgara Tavuk": CHICKEN,
         "Tavuklu Fajita (220 GR)": {"calories": 125.0, "protein": 18.0, "carbs": 9.0, "fat": 2.0},
         "Margherita Pizza": {"calories": 1500.0, "protein": 60.0, "carbs": 180.0, "fat": 58.0},
         "Imkansiz Tabak": {"calories": 9000.0, "protein": 500.0, "carbs": 500.0, "fat": 500.0}},
        {"Kunefe": {"calories": 300.0, "protein": 6.0, "carbs": 40.0, "fat": 14.0}}))
    _patch(monkeypatch, "_estimate_serving_weights_llm",
           lambda items, fallback_weights=None, return_fallbacks=False: ({"Kunefe": 180.0}, set()))

    def llm(items, category_map=None, grams_hint=None):
        if grams_hint:
            return {"Tavuklu Fajita (220 GR)": {"calories": 430.0, "protein": 32.0, "carbs": 28.0, "fat": 18.0}}
        return {"Adana Kebap": {"calories": 650.0, "protein": 45.0, "carbs": 10.0, "fat": 48.0},
                "Mercimek Corbasi": {"calories": 180.0, "protein": 9.0, "carbs": 26.0, "fat": 4.0}}
    _patch(monkeypatch, "_estimate_macros_llm", llm)

    out["analyze_full"] = _analyze(client, {
        "menu_text": MENU_TEXT, "framework_state": '{"menu": 1}',
        "headings": ["Ana Yemekler", "Tatlilar"], "menu_source": "google_drive"})
    out["analyze_full_extract_calls"] = calls

    _patch(monkeypatch, "_extract_categorized_items", lambda *a, **kw: {})
    out["analyze_extraction_empty"] = _analyze(client, {"menu_text": MENU_TEXT})
    _patch(monkeypatch, "_extract_categorized_items", lambda *a, **kw: {"X": ["  ", 7]})
    out["analyze_extraction_blank_items"] = _analyze(client, {"menu_text": MENU_TEXT})
    assert MealLog.query.count() == 1
    return out


def test_web_menu_endpoints_match_pre_refactor_golden(
        client, auth_user, routed_wire, monkeypatch):
    observed = {}
    observed.update(scan_scenarios(client, routed_wire, monkeypatch))
    observed.update(analyze_scenarios(client, auth_user, monkeypatch))
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(observed) <= set(expected)
    for name, value in observed.items():
        assert value == expected[name], name


def test_web_scan_cache_matches_pre_refactor_golden(
        client, auth_user, routed_wire, monkeypatch):
    observed = scan_cache_scenarios(client, routed_wire, monkeypatch)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, value in observed.items():
        assert value == expected[name], name
