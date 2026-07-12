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
    assert "AXIS" in html and "AI</span>" in html      # wordmark
    assert email_templates._VIOLET in html             # tema rengi
    assert "#07070D" in html                           # koyu arka plan
    assert "Find your axis." in html                   # footer
    assert "x.com/axisaiapp" in html                   # sosyal bağlantılar
    assert 'lang="tr"' in html


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
    assert "letter-spacing:6px" in html  # büyük, aralıklı kod kutusu
    assert "Consolas,Menlo,monospace" in html


# ── Güvenlik: kullanıcı girdisi escape edilir ───────────────────────────────

@pytest.mark.parametrize("label,build", [
    ("verification", lambda n: email_templates.verification_code_email(n, _CODE)),
    ("reset", lambda n: email_templates.reset_code_email(n, _CODE)),
    ("welcome", lambda n: email_templates.welcome_email(n)),
    ("password_changed", lambda n: email_templates.password_changed_email(n)),
])
def test_name_is_html_escaped(label, build):
    _, html, _ = build('<img src=x onerror=alert(1)>')
    assert "<img" not in html
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
