"""Auth e-postalarının dili (LP-14) — hesabın kanonik dili sunumu belirler.

Şablon dil SEÇMEZ; çağıran hesabın dilini (User.language) verir. Desteklenen
diller "tr" ve "en"; eksik/geçersiz/desteklenmeyen dil, uygulamanın kanonik
varsayılanına (app.i18n.DEFAULT_LOCALE = "tr") düşer. Dil yalnızca sunumu
değiştirir: kod, bağlantılar ve escape kuralları her dilde aynıdır.

Kapsam:
  - dört şablonun en / tr / fallback matrisi (konu, gövde, düz metin, <html lang>)
  - şablon sabitlerinin app.i18n ile eşitliği (Lambda kopyası app/* import edemez)
  - welcome / password-changed gönderimlerinin User.language'i kullanması
  - Lambda kopyasının iki dilde de aynı çıktıyı üretmesi
  - Lambda'nın (dil sinyali yok) bugünkü varsayılan davranışı

    python -m pytest tests/test_auth_email_language.py -v
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from app.extensions import db
from app.i18n import AVAILABLE_LOCALES, DEFAULT_LOCALE
from app.models import User
from app.services import (
    account_recovery, account_registration, cognito_service, email_service,
    email_templates,
)

_REPO = Path(__file__).resolve().parent.parent
_LAMBDA_SRC = _REPO / "infra" / "cognito-email-sender" / "src"

_CODE = "482913"
_NAME = "yusuf"
_HOSTILE = '<img src=x onerror=alert(1)>'

# Desteklenmeyen/bozuk dil girdileri — hepsi kanonik varsayılana düşer.
_FALLBACK_INPUTS = [None, "", "de", "EN", "en-US", "tr_TR", " en", 1, ["en"]]

# Her dil için konu, gövde (HTML) ve düz metinde bulunması gereken temsilci
# ifadeler; karşı dilin ifadesi bulunmamalı.
_EXPECT = {
    "verification": {
        "tr": ("e-posta doğrulama kodun", "E-postanı doğrula",
               "AxisAI hesabını doğrulamak için kodun"),
        "en": ("your email verification code", "Verify your email",
               "Your code to verify your AxisAI account"),
    },
    "reset": {
        "tr": ("şifre sıfırlama kodun", "şifren değişmedi", "şifren değişmedi"),
        "en": ("your password reset code", "your password has not changed",
               "your password has not changed"),
    },
    "welcome": {
        "tr": ("AxisAI'ye hoş geldin", "Hoş geldin", "hesabın hazır"),
        "en": ("Welcome to AxisAI", "Welcome", "your account is ready"),
    },
    "password_changed": {
        "tr": ("şifren değiştirildi", "Şifren değiştirildi",
               "Hesabının şifresi az önce değiştirildi"),
        "en": ("your password was changed", "Your password was changed",
               "Your account password was just changed"),
    },
}

_CODE_KINDS = ("verification", "reset")


def _build(module, kind, language, name=_NAME):
    if kind == "verification":
        return module.verification_code_email(name, _CODE, language=language)
    if kind == "reset":
        return module.reset_code_email(name, _CODE, language=language)
    if kind == "welcome":
        return module.welcome_email(name, language=language)
    return module.password_changed_email(name, language=language)


def _other(lang):
    return "en" if lang == "tr" else "tr"


# ── Kanonik dil kaynağıyla eşitlik ──────────────────────────────────────────

def test_template_languages_match_the_app_locale_contract():
    """Şablon modülü app.i18n'i import edemez (Lambda'da da çalışır); değerler
    kopyalanmıştır ve kaymamalıdır."""
    assert set(email_templates.SUPPORTED_LANGUAGES) == set(AVAILABLE_LOCALES)
    assert email_templates.DEFAULT_LANGUAGE == DEFAULT_LOCALE
    assert account_registration.DEFAULT_LANGUAGE == DEFAULT_LOCALE
    assert User.__table__.c.language.default.arg == DEFAULT_LOCALE


# ── en / tr matrisi ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("kind", list(_EXPECT))
def test_each_email_renders_in_the_requested_language(kind, lang):
    subject, html, text = _build(email_templates, kind, lang)
    subj_phrase, html_phrase, text_phrase = _EXPECT[kind][lang]
    other_subj, other_html, other_text = _EXPECT[kind][_other(lang)]

    assert subj_phrase in subject and other_subj not in subject
    assert html_phrase in html and other_html not in html
    assert text_phrase in text and other_text not in text
    assert html.startswith('<!DOCTYPE html><html lang="%s">' % lang)
    assert html.count("<html") == 1
    assert "<" not in text


@pytest.mark.parametrize("bad", _FALLBACK_INPUTS)
@pytest.mark.parametrize("kind", list(_EXPECT))
def test_unsupported_language_falls_back_to_the_canonical_default(kind, bad):
    assert _build(email_templates, kind, bad) == \
        _build(email_templates, kind, DEFAULT_LOCALE)


@pytest.mark.parametrize("kind", list(_EXPECT))
def test_omitted_language_is_the_canonical_default(kind):
    """Dil verilmeyen çağrı (bugünkü Lambda çağrısı) kanonik varsayılanı alır."""
    builders = {
        "verification": lambda: email_templates.verification_code_email(_NAME, _CODE),
        "reset": lambda: email_templates.reset_code_email(_NAME, _CODE),
        "welcome": lambda: email_templates.welcome_email(_NAME),
        "password_changed": lambda: email_templates.password_changed_email(_NAME),
    }
    assert builders[kind]() == _build(email_templates, kind, DEFAULT_LOCALE)


# ── Dil, kodu / bağlantıları / güvenlik kurallarını DEĞİŞTİRMEZ ─────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("kind", _CODE_KINDS)
def test_code_in_body_and_text_never_in_subject(kind, lang):
    subject, html, text = _build(email_templates, kind, lang)
    assert _CODE in html and _CODE in text
    assert _CODE not in subject


@pytest.mark.parametrize("kind,path", [
    ("verification", "/verify"), ("reset", "/reset-password"),
    ("welcome", "/login"), ("password_changed", "/forgot-password"),
])
def test_cta_target_is_language_independent(kind, path):
    target = 'href="%s%s"' % (email_templates.APP_BASE_URL, path)
    for lang in ("en", "tr"):
        assert target in _build(email_templates, kind, lang)[1]


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("kind", list(_EXPECT))
def test_name_is_html_escaped_in_every_language(kind, lang):
    _, html, _ = _build(email_templates, kind, lang, name=_HOSTILE)
    assert "<img" not in html
    assert "&lt;img" in html


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("kind", _CODE_KINDS)
def test_code_is_html_escaped_in_every_language(kind, lang):
    if kind == "verification":
        _, html, _ = email_templates.verification_code_email(
            _NAME, "<b>1</b>", language=lang)
    else:
        _, html, _ = email_templates.reset_code_email(
            _NAME, "<b>1</b>", language=lang)
    assert "<b>1</b>" not in html
    assert "&lt;b&gt;1&lt;/b&gt;" in html


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("kind", list(_EXPECT))
def test_missing_name_uses_neutral_greeting(kind, lang):
    subject, html, text = _build(email_templates, kind, lang, name=None)
    for part in (subject, html, text):
        assert "None" not in part


def test_english_welcome_subject_greets_by_name():
    assert email_templates.welcome_email("yusuf", language="en")[0] == \
        "Welcome to AxisAI, yusuf!"
    assert email_templates.welcome_email(None, language="en")[0] == \
        "Welcome to AxisAI!"


@pytest.mark.parametrize("lang,support", [("en", "Questions?"), ("tr", "Soruların için:")])
def test_password_changed_keeps_support_contact_and_advice(lang, support):
    _, html, text = email_templates.password_changed_email(_NAME, language=lang)
    assert "hello@axisaiapp.com" in html and "hello@axisaiapp.com" in text
    assert support in html
    assert "/forgot-password" in text


def test_english_copy_has_no_turkish_characters():
    """İngilizce şablonlarda Türkçe metin sızıntısı kalmamalı."""
    for kind in _EXPECT:
        for part in _build(email_templates, kind, "en"):
            assert not set(part) & set("çğıöşüÇĞİÖŞÜ"), (kind, part)


# ── Uygulama tarafı gönderimler: dil User.language'den gelir ────────────────

@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_send(to, subject, html, text=None, **kwargs):
        calls.append({"to": to, "subject": subject, "html": html, "text": text})
        return "msg-1"

    monkeypatch.setattr(email_service, "send_html_email", fake_send)
    return calls


@pytest.fixture
def registration_provider(monkeypatch):
    monkeypatch.setattr(cognito_service, "sign_up",
                        lambda username, password, email, name: "sub-" + username)
    monkeypatch.setattr(cognito_service, "confirm_sign_up",
                        lambda username, code: None)


@pytest.mark.parametrize("registered,expected", [
    ("en", "en"), ("tr", "tr"), (None, DEFAULT_LOCALE), ("de", DEFAULT_LOCALE),
])
def test_welcome_email_follows_the_registration_language(
        app, sent, registration_provider, registered, expected):
    account_registration.register_account(
        "dilci", "dilci@example.com", "Sifre1234", language=registered)
    assert User.query.filter_by(username="dilci").one().language == expected

    account_registration.confirm_account("dilci", "123456")

    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == "dilci@example.com"
    assert mail["html"].startswith('<!DOCTYPE html><html lang="%s">' % expected)
    assert _EXPECT["welcome"][expected][0] in mail["subject"]
    assert _EXPECT["welcome"][expected][2] in mail["text"]


@pytest.mark.parametrize("stored,expected", [
    ("en", "en"), ("tr", "tr"), (None, DEFAULT_LOCALE), ("xx", DEFAULT_LOCALE),
])
def test_password_changed_email_follows_the_account_language(
        app, sent, make_user, stored, expected):
    user = make_user("sifreci", email="sifreci@example.com")
    user.language = stored
    db.session.commit()

    account_recovery._send_password_changed_email(user.id)

    assert len(sent) == 1
    mail = sent[0]
    assert mail["to"] == "sifreci@example.com"
    assert mail["html"].startswith('<!DOCTYPE html><html lang="%s">' % expected)
    assert _EXPECT["password_changed"][expected][0] in mail["subject"]
    assert _EXPECT["password_changed"][expected][2] in mail["text"]


def test_password_reset_flow_sends_the_notice_in_the_account_language(
        app, sent, make_user, monkeypatch):
    make_user("resetci", email="resetci@example.com", language="en")
    monkeypatch.setattr(cognito_service, "confirm_forgot_password",
                        lambda username, code, new_password: None)
    monkeypatch.setattr(cognito_service, "revoke_token", lambda token: None)

    account_recovery.reset_password("resetci", "123456", "Newpass123")

    assert [m["subject"] for m in sent] == ["AxisAI — your password was changed"]


def test_language_does_not_change_who_is_emailed_or_whether(app, sent, make_user):
    """Dil yalnızca sunumdur: e-postasız hesap hiçbir dilde e-posta almaz."""
    user = make_user("epostasiz", language="en")
    user.email = ""
    db.session.commit()
    account_recovery._send_password_changed_email(user.id)
    assert sent == []


# ── Uygulama / Lambda eşitliği ──────────────────────────────────────────────

def _load_lambda_templates():
    spec = importlib.util.spec_from_file_location(
        "lambda_email_templates", _LAMBDA_SRC / "email_templates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("lang", ["en", "tr", None, "de"])
@pytest.mark.parametrize("kind", list(_EXPECT))
def test_lambda_copy_renders_identically_in_every_language(kind, lang):
    lambda_templates = _load_lambda_templates()
    assert _build(lambda_templates, kind, lang) == \
        _build(email_templates, kind, lang)


@pytest.fixture
def lambda_handler(monkeypatch):
    if str(_LAMBDA_SRC) not in sys.path:
        monkeypatch.syspath_prepend(str(_LAMBDA_SRC))
    import email_sender
    import handler

    monkeypatch.setattr(handler, "_decrypt_code", lambda ciphertext: _CODE)
    monkeypatch.setattr(email_sender, "RESEND_API_KEY", "re_test_key")
    posts = []
    monkeypatch.setattr(
        email_sender, "_post_json",
        lambda url, payload, headers, timeout=4: posts.append(payload) or {"id": "m"})
    return handler, posts


@pytest.mark.parametrize("trigger,kind", [
    ("CustomEmailSender_SignUp", "verification"),
    ("CustomEmailSender_ResendCode", "verification"),
    ("CustomEmailSender_ForgotPassword", "reset"),
])
def test_lambda_without_a_language_signal_keeps_the_canonical_default(
        lambda_handler, trigger, kind):
    """Cognito olayı bugün dil taşımıyor (SignUp yalnızca email+name yazar,
    ClientMetadata gönderilmez). Lambda dil UYDURMAZ: kanonik varsayılanla
    gönderir — LP-14 öncesiyle bayt-bayt aynı."""
    handler, posts = lambda_handler
    event = {"version": "1", "triggerSource": trigger, "userName": "ali",
             "request": {"type": "customEmailSenderRequestV1", "code": "Y2lwaGVy",
                         "userAttributes": {"email": "ali@example.com", "name": "Ali"}}}
    handler.handler(event, None)
    assert len(posts) == 1
    subject, html, text = _build(email_templates, kind, DEFAULT_LOCALE, name="Ali")
    assert (posts[0]["subject"], posts[0]["html"], posts[0]["text"]) == \
        (subject, html, text)
