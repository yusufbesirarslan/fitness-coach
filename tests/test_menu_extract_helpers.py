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
