"""app/services/email_templates.py — markalı auth e-posta şablonu testleri.

Hermetik: şablonlar saf fonksiyondur, ağ/SDK yoktur. Her şablonun
(subject, html, text) sözleşmesi, marka öğeleri ve güvenlik kuralları
(kod konu satırında olmaz, kullanıcı adı HTML-escape edilir) sınanır.
"""
import pytest

from app.services import email_templates

_CODE = "123456"
_NAME = "yusuf"

_CODE_TEMPLATES = [
    email_templates.verification_code_email,
    email_templates.reset_code_email,
]
_ALL_TEMPLATES = [
    ("verification", lambda: email_templates.verification_code_email(_NAME, _CODE)),
    ("reset", lambda: email_templates.reset_code_email(_NAME, _CODE)),
    ("welcome", lambda: email_templates.welcome_email(_NAME)),
    ("password_changed", lambda: email_templates.password_changed_email(_NAME)),
]


# ── Sözleşme: (subject, html, text) + marka kabuğu ──────────────────────────

@pytest.mark.parametrize("label,build", _ALL_TEMPLATES)
def test_returns_subject_html_text_tuple(label, build):
    result = build()
    assert isinstance(result, tuple) and len(result) == 3
    subject, html, text = result
    assert subject and isinstance(subject, str)
    assert html.startswith("<!DOCTYPE html>")
    assert text and "<" not in text  # düz metin HTML içermez


@pytest.mark.parametrize("label,build", _ALL_TEMPLATES)
def test_shared_branding_in_every_email(label, build):
    _, html, _ = build()
    # Axis Signal kilidi: resmi holo işaret + "AxisAI" adı (mobil BrandMark).
    assert email_templates.BRAND_MARK_URL == "https://www.axisaiapp.com/icon-holo.png"
    assert 'src="%s"' % email_templates.BRAND_MARK_URL in html
    assert '>AxisAI</td>' in html
    assert email_templates._AXIS == "#2A62F5"          # tek marka mavisi (CTA)
    assert "background-color:#2A62F5" in html
    assert email_templates._CANVAS in html             # açık tema satır-içi
    assert email_templates._DARK_CANVAS in html        # koyu tema <style>'da
    assert "prefers-color-scheme:dark" in html
    assert "Find your axis." in html                   # footer
    assert "x.com/axisaiapp" in html                   # sosyal bağlantılar
    assert 'lang="tr"' in html


@pytest.mark.parametrize("label,build", _ALL_TEMPLATES)
def test_retired_aurora_identity_is_gone(label, build):
    """BRAND-1: eski mor/camgöbeği Aurora Axis paleti ve AXIS·AI wordmark'ı yok."""
    _, html, _ = build()
    for legacy in ("#7C5CFF", "#07070D", "#0F1020", "#23264a", "#38E1FF",
                   "AXIS<span", "text-transform:uppercase"):
        assert legacy.lower() not in html.lower(), legacy


@pytest.mark.parametrize("label,build", _ALL_TEMPLATES)
def test_only_remote_resource_is_the_static_brand_mark(label, build):
    """İzleme pikseli yok: tek <img> statik, alıcıdan bağımsız marka işaretidir."""
    _, html, _ = build()
    assert html.count("<img") == 1
    assert "@import" not in html and "url(" not in html
    assert "fonts.googleapis" not in html and "gstatic" not in html


@pytest.mark.parametrize("label,build", _ALL_TEMPLATES)
def test_cta_uses_app_base_url(label, build):
    _, html, _ = build()
    assert email_templates.APP_BASE_URL in html


def test_cta_targets():
    assert email_templates.APP_BASE_URL + "/verify" in \
        email_templates.verification_code_email(_NAME, _CODE)[1]
    assert email_templates.APP_BASE_URL + "/reset-password" in \
        email_templates.reset_code_email(_NAME, _CODE)[1]
    assert email_templates.APP_BASE_URL + "/login" in \
        email_templates.welcome_email(_NAME)[1]
    assert email_templates.APP_BASE_URL + "/forgot-password" in \
        email_templates.password_changed_email(_NAME)[1]


# ── Kod içeren şablonlar: kod gövdede VAR, konuda ASLA yok ──────────────────

@pytest.mark.parametrize("build", _CODE_TEMPLATES)
def test_code_in_body_and_text_never_in_subject(build):
    subject, html, text = build(_NAME, _CODE)
    assert _CODE in html
    assert _CODE in text
    assert _CODE not in subject  # konu satırları loglanır — kod sızmamalı


@pytest.mark.parametrize("build", _CODE_TEMPLATES)
def test_code_block_is_prominent(build):
    _, html, _ = build(_NAME, _CODE)
    assert "font-size:32px" in html and "letter-spacing:8px" in html  # büyük, aralıklı
    assert email_templates._MONO in html
    assert 'class="ax-code"' in html


@pytest.mark.parametrize("build", _CODE_TEMPLATES)
def test_code_never_in_inbox_preheader(build):
    """Gizli önizleme metni kodu taşımaz (bildirim/önizleme satırına düşmez)."""
    _, html, _ = build(_NAME, _CODE)
    start = html.index('<div style="display:none')
    preheader = html[start:html.index("</div>", start)]
    assert _CODE not in preheader
    assert html.index(_CODE) > html.index("</div>", start)


# ── Güvenlik: kullanıcı girdisi escape edilir ───────────────────────────────

@pytest.mark.parametrize("label,build", [
    ("verification", lambda n: email_templates.verification_code_email(n, _CODE)),
    ("reset", lambda n: email_templates.reset_code_email(n, _CODE)),
    ("welcome", lambda n: email_templates.welcome_email(n)),
    ("password_changed", lambda n: email_templates.password_changed_email(n)),
])
def test_name_is_html_escaped(label, build):
    _, html, _ = build('<img src=x onerror=alert(1)>')
    # Tek <img> statik marka işaretidir; saldırgan isim asla etiket olmaz.
    assert "<img src=x" not in html and "onerror=alert(1)>" not in html
    assert html.count("<img") == 1
    assert "&lt;img" in html


@pytest.mark.parametrize("label,build", [
    ("verification", lambda n: email_templates.verification_code_email(n, _CODE)),
    ("welcome", lambda n: email_templates.welcome_email(n)),
])
def test_missing_name_falls_back_gracefully(label, build):
    subject, html, _ = build(None)
    assert "None" not in html
    assert "None" not in subject


# ── İçerik gereksinimleri ───────────────────────────────────────────────────

def test_welcome_subject_greets_by_name():
    assert "yusuf" in email_templates.welcome_email("yusuf")[0]


def test_password_changed_has_support_contact_and_security_advice():
    _, html, text = email_templates.password_changed_email(_NAME)
    assert "hello@axisaiapp.com" in html
    assert "hello@axisaiapp.com" in text
    assert "sıfırla" in html  # "sen yapmadıysan" güvenlik tavsiyesi

def test_reset_email_says_password_unchanged_if_not_requested():
    _, html, text = email_templates.reset_code_email(_NAME, _CODE)
    assert "şifren değişmedi" in html
    assert "şifren değişmedi" in text
