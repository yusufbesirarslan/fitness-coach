"""Tests for menu HTML extraction helpers (app/services/menu_extract.py).

Mevcut truncation/window testlerini tamamlar: framework-state ayıklama,
alt-link keşfi, JSON-LD/başlık/konteyner bölümleme, WordPress REST yedeği,
deterministik menü skoru ve Pump Check sözleşmesi.

    python -m pytest tests/test_menu_extract_helpers.py -v
"""
import json

from bs4 import BeautifulSoup
from urllib.parse import urlparse

from app.services import menu_extract
from app.services.menu_extract import (
    _content_has_food_items,
    _discover_menu_links,
    _extract_framework_state,
    _extract_page_sections,
    _menu_score,
    _try_wordpress_api,
    validate_pump_check,
)


# ---------------------------------------------------------------------------
# Framework state ayıklama
# ---------------------------------------------------------------------------

def test_next_data_script_tag_parsed():
    html = '<html><script id="__NEXT_DATA__">{"props": {"menu": ["Kebap"]}}</script></html>'
    state, fw = _extract_framework_state(html)
    assert fw == "next"
    assert state["props"]["menu"] == ["Kebap"]


def test_window_nuxt_assignment_parsed_with_brace_counting():
    html = '<script>window.__NUXT__ = {"data": {"x": {"y": 1}}};</script>'
    state, fw = _extract_framework_state(html)
    assert fw == "nuxt"
    assert state == {"data": {"x": {"y": 1}}}


def test_invalid_state_json_returns_none():
    assert _extract_framework_state('<script id="__NEXT_DATA__">{bozuk</script>') == (None, None)
    assert _extract_framework_state("<html><p>menü</p></html>") == (None, None)


# ---------------------------------------------------------------------------
# Alt-link keşfi
# ---------------------------------------------------------------------------

def test_discover_menu_links_filters_and_normalizes():
    base = urlparse("https://restoran.example/")
    soup = BeautifulSoup("""
        <a href="/menu/kahvalti">Kahvaltı</a>
        <a href="https://restoran.example/icecekler?utm=x">İçecekler</a>
        <a href="https://baska-site.example/menu">Dış site</a>
        <a href="#fragment">Atla</a>
        <a href="javascript:void(0)">JS</a>
        <a href="mailto:a@b.co">Mail</a>
        <a href="/hakkimizda">Menü dışı</a>
    """, "html.parser")
    links = _discover_menu_links(soup, base)
    assert set(links) == {"https://restoran.example/menu/kahvalti",
                          "https://restoran.example/icecekler"}


# ---------------------------------------------------------------------------
# Sayfa bölümleme
# ---------------------------------------------------------------------------

def test_sections_prefer_jsonld_menu():
    ld = {"@type": "Restaurant", "hasMenuSection": [
        {"name": "Çorbalar", "hasMenuItem": [{"name": "Mercimek"}, {"name": "Ezogelin"}]}]}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script><h2>Yoksay</h2>'
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections(html, soup)
    assert sections == [{"category": "Çorbalar", "text": "Mercimek\nEzogelin"}]


def test_sections_group_by_headings_and_drop_price_noise():
    html = """
    <h2>Ana Yemekler</h2><li>Adana Kebap</li><li>120</li><li>₺250</li>
    <h2>Tatlılar</h2><li>Künefe</li><li>Künefe</li>
    """
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections(html, soup)
    by_cat = {s["category"]: s["text"] for s in sections}
    assert by_cat["Ana Yemekler"] == "Adana Kebap"     # fiyat satırları düştü
    assert by_cat["Tatlılar"] == "Künefe"               # kategori içi tekilleştirme


def test_sections_capture_div_wrapped_items_without_headings():
    html = '<div class="menu-list"><p>Lahmacun</p><p>Pide</p></div>'
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections("<html></html>", soup)
    assert len(sections) == 1
    assert sections[0]["category"] == "Genel"
    assert "Lahmacun" in sections[0]["text"]
    assert "Pide" in sections[0]["text"]


def test_sections_jsonld_accepts_single_dict_shapes():
    # hasMenuSection ve hasMenuItem TEK dict (liste değil) gelebilir → sarmalanmalı.
    ld = {"@type": "Menu",
          "hasMenuSection": {"name": "İçecekler",
                             "hasMenuItem": {"name": "Türk Kahvesi"}}}
    html = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections(html, soup)
    assert sections == [{"category": "İçecekler", "text": "Türk Kahvesi"}]


def test_sections_jsonld_malformed_falls_through_to_heading_scan():
    # Bozuk JSON-LD yutulur (TypeError/JSONDecodeError) → başlık taramasına düşer.
    html = ('<script type="application/ld+json">{bozuk json}</script>'
            '<h2>Salatalar</h2><li>Sezar Salata</li>')
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections(html, soup)
    assert {s["category"]: s["text"] for s in sections}["Salatalar"] == "Sezar Salata"


def test_sections_main_body_fallback_when_no_container_class():
    # Başlık yok, menu/food sınıflı konteyner yok → main/article/body son çare.
    html = "<main><p>Köfte Ekmek</p><span>Ayran</span><p>ab</p></main>"
    soup = BeautifulSoup(html, "html.parser")
    sections = _extract_page_sections("<html></html>", soup)
    assert len(sections) == 1
    assert sections[0]["category"] == "Genel"
    assert "Köfte Ekmek" in sections[0]["text"]
    assert "Ayran" in sections[0]["text"]
    assert "ab" not in sections[0]["text"]  # <3 karakter elendi


# ---------------------------------------------------------------------------
# İçerik kalitesi
# ---------------------------------------------------------------------------

def test_content_has_food_items_threshold():
    assert _content_has_food_items("kahvaltı tabağı, mercimek çorba ve pizza") is True
    assert _content_has_food_items("hakkımızda, iletişim, kariyer") is False
    assert _content_has_food_items("pizza") is False        # tek anahtar yetmez
    assert _content_has_food_items("pizza burger", threshold=2) is True


# ---------------------------------------------------------------------------
# WordPress REST yedeği
# ---------------------------------------------------------------------------

class _WpResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_wordpress_api_skipped_for_non_wp_sites(app):
    base = urlparse("https://restoran.example/menu")
    assert _try_wordpress_api(base, "<html>düz site</html>") == (None, [])


def test_wordpress_api_recovers_menu_sections(app, monkeypatch):
    rendered = ("<h2>Kebaplar</h2><li>Adana Kebap Dürüm</li><li>Urfa Kebap Porsiyon</li>"
                "<li>Mercimek Çorba</li><li>Izgara Tavuk Salata</li>"
                "<li>Margherita Pizza</li><li>Ev Yapımı Burger Menü</li>")
    pages = [{"title": {"rendered": "Menümüz"}, "content": {"rendered": rendered}}]

    def fake_get(url, **kwargs):
        return _WpResp(pages if "slug=menu" in url else [])
    monkeypatch.setattr(menu_extract, "_safe_requests_get", fake_get)

    base = urlparse("https://restoran.example/menu")
    title, sections = _try_wordpress_api(base, '<html class="wp-content">wp-json</html>')
    assert title == "Menümüz"
    assert any(s["category"] == "Kebaplar" for s in sections)


def test_wordpress_api_handles_total_failure(app, monkeypatch):
    def boom(url, **kwargs):
        raise ValueError("İç ağ adresleri engellendi.")
    monkeypatch.setattr(menu_extract, "_safe_requests_get", boom)
    base = urlparse("https://restoran.example/menu")
    assert _try_wordpress_api(base, "wp-json her yerde") == (None, [])


def test_wordpress_api_skips_non200_and_empty_content(app, monkeypatch):
    food_html = ("<li>Adana Kebap</li><li>Mercimek Çorba</li>"
                 "<li>Pizza Burger Makarna Tavuk</li>" + "dolgu metin " * 20)
    good = {"title": {"rendered": "Yiyecekler"}, "content": {"rendered": food_html}}

    def fake_get(url, **kw):
        if "slug=menu" in url:
            return _WpResp([], status=404)                 # non-200 → continue
        if "slug=yemek" in url:
            return _WpResp([{"title": {"rendered": ""},
                             "content": {"rendered": ""}}])  # boş içerik → continue
        if "slug=yiyecekler" in url:
            return _WpResp([good])
        return _WpResp([])
    monkeypatch.setattr(menu_extract, "_safe_requests_get", fake_get)

    base = urlparse("https://restoran.example/menu")
    title, sections = _try_wordpress_api(base, "wp-content burada")
    assert title == "Yiyecekler"
    assert sections  # 'yiyecekler' slug'ından toparlandı


def test_wordpress_api_discovers_and_fetches_subpages(app, monkeypatch):
    # İlk sayfa uzun ama YEMEK içermez; alt-sayfa linki keşfedilip ayrıca çekilir.
    filler = "Hakkımızda kurumsal bilgi metni burada uzun uzun yazılı " * 6
    first = {"title": {"rendered": "Ana"}, "content": {"rendered":
             filler + '<a href="/menu/kebaplar">Kebaplar</a>'}}
    sub_html = ("<h2>Kebaplar</h2><li>Adana Kebap Dürüm Porsiyon</li>"
                "<li>Urfa Kebap Izgara Tavuk Salata</li>"
                "<li>Mercimek Çorba Pizza Burger Makarna</li>" + "ek satır " * 30)
    subpage = {"title": {"rendered": "Kebaplar"}, "content": {"rendered": sub_html}}

    def fake_get(url, **kw):
        if "slug=kebaplar" in url:
            return _WpResp([subpage])
        if "slug=menu" in url:
            return _WpResp([first])
        return _WpResp([])  # diğer ana slug'lar boş
    monkeypatch.setattr(menu_extract, "_safe_requests_get", fake_get)

    base = urlparse("https://restoran.example/menu")
    title, sections = _try_wordpress_api(base, '<html>wp-json</html>')
    assert any(s["category"] == "Kebaplar" for s in sections)  # alt-sayfa toparlandı


def test_wordpress_api_subpage_fetch_error_is_swallowed(app, monkeypatch):
    filler = "Genel kurumsal tanıtım metni epeyce uzun bir paragraf halinde " * 6
    first = {"title": {"rendered": "Ana"}, "content": {"rendered":
             filler + '<a href="/menu/kebaplar">Kebaplar</a>'}}

    def fake_get(url, **kw):
        if "slug=kebaplar" in url:
            raise ValueError("İç ağ adresleri engellendi.")  # alt-sayfa hatası yutulur
        if "slug=menu" in url:
            return _WpResp([first])
        return _WpResp([])
    monkeypatch.setattr(menu_extract, "_safe_requests_get", fake_get)

    base = urlparse("https://restoran.example/menu")
    # Hiç yemekli bölüm bulunamaz ama çağrı patlamadan (None, []) döner.
    assert _try_wordpress_api(base, "wp-json") == (None, [])


# ---------------------------------------------------------------------------
# Menü skoru + Pump Check sözleşmesi
# ---------------------------------------------------------------------------

def test_menu_score_contract_and_turkish_reason():
    remaining = {"calories": 800, "protein": 60, "carbs": 80, "fat": 30}
    score, warnings, reason = _menu_score(
        {"calories": 350, "protein": 45, "carbs": 10, "fat": 8}, remaining)
    assert isinstance(score, int) and 0 <= score <= 100
    assert isinstance(warnings, list)
    assert reason and not any(w in reason for w in ("High", "Low", "Exceeds"))  # Türkçe


def test_menu_score_overbudget_warns_in_turkish():
    remaining = {"calories": 200, "protein": 20, "carbs": 20, "fat": 10}
    score, warnings, reason = _menu_score(
        {"calories": 1500, "protein": 30, "carbs": 150, "fat": 70}, remaining)
    assert any("bütçe" in w.lower() or "limit" in w.lower() for w in warnings)


def test_menu_score_flags_low_protein_food_in_turkish():
    # Yüksek karbonhidrat/yağ, çok az protein → 'low_protein_food' → "Protein oranı düşük".
    remaining = {"calories": 1200, "protein": 80, "carbs": 120, "fat": 40}
    score, warnings, reason = _menu_score(
        {"calories": 400, "protein": 2, "carbs": 55, "fat": 16}, remaining)
    assert "Protein oranı düşük" in reason


def test_pump_check_contract(app):
    result = validate_pump_check(b"img", "salon", "ağırlık bölgesi")
    # F7: mock doğrulama "doğrulandı" İDDİA ETMEZ; dürüst "kaydedildi" mesajı.
    assert result["valid"] is True
    assert result["fallback"] is False
    assert "doğruland" not in result["reason"].lower()
    assert "kaydedildi" in result["reason"].lower()
    # Boş girdiler de biçim hatasız işlenmeli.
    result = validate_pump_check(b"img", None, "")
    assert result["valid"] is True
