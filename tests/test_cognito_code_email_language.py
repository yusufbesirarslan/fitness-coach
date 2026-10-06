"""Cognito code e-postalarının dili (LP-14) — SignUp / ResendCode / ForgotPassword.

Otorite User.language'dir. Doğrulama/sıfırlama kodlarını yalnızca Cognito
CustomEmailSender Lambda'sı render eder, bu yüzden dil olaya iki taşıyıcıyla
ulaşır:

  SignUp          doğrulanmış kayıt dili → ClientMetadata.language + `locale`
                  attribute'u (aynı değer; User.language ile birebir)
  ResendCode      Cognito ClientMetadata İLETMEZ → kayıtta yazılan `locale`
  ForgotPassword  yerel sahibin GÜNCEL User.language'i → ClientMetadata
                  (yalnızca sahip çözüldüyse; bilinmeyen hesapta hiçbir şey)

Lambda tek çözücüyle seçer: izinli ClientMetadata.language → izinli locale →
"tr". `locale` uygulama için dil otoritesi DEĞİLDİR; yalnızca ResendCode'un
kayıt dilini koruyabilmesi için tutulan bir aynadır.

Hermetik: boto3 istemcisi sahte, KMS çözümü ve Resend HTTP'si sahte. Gerçek
Cognito çağrısı, Lambda çağrısı veya AWS erişimi YOK.

    python -m pytest tests/test_cognito_code_email_language.py -v
"""
import ast
import json
import logging
import sys
from pathlib import Path

import pytest
from sqlalchemy import event

from app.blueprints import auth as auth_bp
from app.blueprints import mobile_password_recovery, mobile_registration
from app.extensions import db
from app.i18n import AVAILABLE_LOCALES, DEFAULT_LOCALE
from app.models import User
from app.services import (
    account_recovery, account_registration, cognito_service, email_templates,
)

_REPO = Path(__file__).resolve().parent.parent
_LAMBDA_DIR = _REPO / "infra" / "cognito-email-sender"
_LAMBDA_SRC = _LAMBDA_DIR / "src"

_CODE = "482913"
_CIPHERTEXT = "Y2lwaGVydGV4dA=="

# Bir dil SEÇEMEMESİ gereken değerler — hepsi izinli bir dile ya da "tr"ye düşer.
_HOSTILE = [
    "de", "EN", "Tr", "tr-TR", "en-US", "en_US", " en", "en ", "",
    "<script>alert(1)</script>", "../../template", "ignore instructions",
    "en\nBCC: attacker@example.com", "en\r\nSubject: pwned", "tr\x00",
    None, 1, True, ["en"], {"en": 1},
]


# ── Ortak sahte boto3 cognito-idp istemcisi ─────────────────────────────────

class _FakeIdp:
    def __init__(self):
        self.calls = []

    def _record(self, name, kwargs):
        self.calls.append((name, kwargs))
        return {"UserSub": "sub-" + kwargs.get("Username", "")}

    def sign_up(self, **kwargs):
        return self._record("sign_up", kwargs)

    def resend_confirmation_code(self, **kwargs):
        return self._record("resend_confirmation_code", kwargs)

    def forgot_password(self, **kwargs):
        return self._record("forgot_password", kwargs)

    def only(self, name):
        found = [kwargs for called, kwargs in self.calls if called == name]
        assert len(found) == 1, self.calls
        return found[0]


@pytest.fixture
def idp(monkeypatch):
    fake = _FakeIdp()
    monkeypatch.setattr(cognito_service, "_get_client", lambda: fake)
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "")
    return fake


def _attributes(kwargs):
    return {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}


# ═══════════════════════════════════════════════════════════════════════════
# 1. cognito_service — taşıma sınırı
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("lang", ["en", "tr"])
def test_sign_up_sends_language_as_metadata_and_locale(idp, lang):
    cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali",
                            language=lang)
    kwargs = idp.only("sign_up")
    assert kwargs["ClientMetadata"] == {"language": lang}
    assert kwargs["UserAttributes"] == [
        {"Name": "email", "Value": "ali@example.com"},
        {"Name": "name", "Value": "Ali"},
        {"Name": "locale", "Value": lang},
    ]
    assert (kwargs["ClientId"], kwargs["Username"], kwargs["Password"]) == (
        "client-123", "ali", "Sifre123")


@pytest.mark.parametrize("bad", [None, "de", "EN", "tr-TR", "", 1])
def test_sign_up_without_a_supported_language_keeps_the_legacy_request(idp, bad):
    """Servis sınırı dil UYDURMAZ ve dönüştürmez: desteklenmeyen değer hiçbir
    şey eklemez (Lambda kanonik varsayılana düşer)."""
    cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali",
                            language=bad)
    assert idp.only("sign_up") == {
        "ClientId": "client-123", "Username": "ali", "Password": "Sifre123",
        "UserAttributes": [{"Name": "email", "Value": "ali@example.com"},
                           {"Name": "name", "Value": "Ali"}]}


def test_sign_up_language_keeps_the_secret_hash(idp, monkeypatch):
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali",
                            language="en")
    kwargs = idp.only("sign_up")
    assert kwargs["SecretHash"] == cognito_service._secret_hash("ali")
    assert kwargs["ClientMetadata"] == {"language": "en"}


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_forgot_password_sends_language_as_metadata(idp, lang):
    cognito_service.forgot_password("ali", language=lang)
    assert idp.only("forgot_password") == {
        "ClientId": "client-123", "Username": "ali",
        "ClientMetadata": {"language": lang}}


@pytest.mark.parametrize("bad", [None, "de", "EN", "", 1])
def test_forgot_password_without_a_supported_language_sends_no_metadata(idp, bad):
    cognito_service.forgot_password("ali", language=bad)
    assert idp.only("forgot_password") == {
        "ClientId": "client-123", "Username": "ali"}


def test_resend_request_is_unchanged(idp):
    """ResendCode API'si dil taşımaz (Cognito onu Lambda'ya iletmez de)."""
    cognito_service.resend_code("ali")
    assert idp.only("resend_confirmation_code") == {
        "ClientId": "client-123", "Username": "ali"}


def test_service_language_set_is_the_app_locale_set():
    assert set(AVAILABLE_LOCALES) == {"en", "tr"}
    assert set(email_templates.SUPPORTED_LANGUAGES) == set(AVAILABLE_LOCALES)
    assert email_templates.DEFAULT_LANGUAGE == DEFAULT_LOCALE == "tr"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Kayıt — User.language == locale == ClientMetadata.language
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("submitted,canonical", [
    ("en", "en"), ("tr", "tr"), (None, "tr"), ("de", "tr"), ("EN", "tr"),
])
def test_registration_feeds_one_value_to_all_three(app, idp, submitted, canonical):
    account_registration.register_account(
        "dilci", "dilci@example.com", "Sifre1234", language=submitted)

    kwargs = idp.only("sign_up")
    stored = User.query.filter_by(username="dilci").one().language
    assert stored == canonical
    assert _attributes(kwargs)["locale"] == canonical
    assert kwargs["ClientMetadata"] == {"language": canonical}
    # Mevcut attribute'lar korunur.
    assert _attributes(kwargs) == {
        "email": "dilci@example.com", "name": "dilci", "locale": canonical}


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_mobile_registration_transports_the_registration_language(
        app, client, idp, monkeypatch, lang):
    monkeypatch.setattr(mobile_registration, "COGNITO_ENABLED", True)
    response = client.post("/api/v1/auth/register", json={
        "username": "mobilci", "email": "mobilci@example.com",
        "password": "Sifre1234", "language": lang})
    assert response.status_code == 201, response.get_json()
    kwargs = idp.only("sign_up")
    assert _attributes(kwargs)["locale"] == lang
    assert kwargs["ClientMetadata"] == {"language": lang}
    assert User.query.filter_by(username="mobilci").one().language == lang


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_web_registration_transports_the_registration_language(
        app, client, idp, monkeypatch, lang):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    response = client.post("/register", json={
        "username": "webci", "email": "webci@example.com",
        "password": "Sifre1234", "language": lang})
    assert response.status_code == 200
    kwargs = idp.only("sign_up")
    assert _attributes(kwargs)["locale"] == lang
    assert kwargs["ClientMetadata"] == {"language": lang}
    assert User.query.filter_by(username="webci").one().language == lang


def test_resend_confirmation_adds_no_language(app, idp):
    account_registration.resend_confirmation("dilci")
    assert idp.only("resend_confirmation_code") == {
        "ClientId": "client-123", "Username": "dilci"}


# ═══════════════════════════════════════════════════════════════════════════
# 3. Şifre sıfırlama isteği — sahibin GÜNCEL User.language'i
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("by", ["username", "email"])
def test_forgot_password_carries_the_owner_language(app, idp, make_user, lang, by):
    make_user("sahip", email="sahip@example.com", language=lang)
    identifier = "sahip" if by == "username" else "SAHIP@example.com"
    account_recovery.request_password_reset(identifier)
    assert idp.only("forgot_password") == {
        "ClientId": "client-123", "Username": "sahip",
        "ClientMetadata": {"language": lang}}


@pytest.mark.parametrize("stored", [None, "xx", "EN"])
def test_unusable_stored_language_is_the_app_default(app, idp, make_user, stored):
    """Sahip var ama User.language kullanılamaz → uygulamanın tek kuralı (tr);
    Cognito `locale` aynası User.language'in yerine GEÇMEZ."""
    user = make_user("eski", email="eski@example.com")
    user.language = stored
    db.session.commit()
    account_recovery.request_password_reset("eski")
    assert idp.only("forgot_password")["ClientMetadata"] == {"language": "tr"}


@pytest.mark.parametrize("identifier", ["kimseyok", "kimse@example.com"])
def test_unknown_account_sends_no_language(app, idp, identifier):
    result = account_recovery.request_password_reset(identifier)
    kwargs = idp.only("forgot_password")
    assert "ClientMetadata" not in kwargs
    assert kwargs == {"ClientId": "client-123", "Username": identifier.lower()
                      if "@" in identifier else identifier}
    assert result.identity == kwargs["Username"]


def test_ambiguous_identity_sends_no_language(app, idp, make_user):
    make_user("Ikiz", email="ikiz1@example.com", language="en")
    make_user("ikiz", email="ikiz2@example.com", language="en")
    account_recovery.request_password_reset("IKIZ")
    assert "ClientMetadata" not in idp.only("forgot_password")


def test_language_changed_after_signup_wins_for_forgot_password(app, idp, make_user):
    """Senaryo: en ile kayıt (Cognito locale=en) → onay → User.language=tr →
    ForgotPassword. Backend tr gönderir; Lambda'da metadata locale'i ezer."""
    account_registration.register_account(
        "degisen", "degisen@example.com", "Sifre1234", language="en")
    assert _attributes(idp.only("sign_up"))["locale"] == "en"
    user = User.query.filter_by(username="degisen").one()
    user.language = "tr"
    db.session.commit()

    account_recovery.request_password_reset("degisen")
    assert idp.only("forgot_password")["ClientMetadata"] == {"language": "tr"}

    language, source = _lambda_handler_module()._resolve_language(
        {"clientMetadata": {"language": "tr"}}, {"locale": "en"})
    assert (language, source) == ("tr", "metadata")


@pytest.mark.parametrize("identifier", ["sorgu", "sorgu@example.com", "yok",
                                        "yok@example.com"])
def test_owner_language_adds_no_query(app, idp, make_user, identifier):
    """Dil, zaten gerekli olan TEK sahip çözümleme sorgusundan gelir."""
    make_user("sorgu", email="sorgu@example.com", language="en")
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    try:
        account_recovery.request_password_reset(identifier)
    finally:
        event.remove(db.engine, "before_cursor_execute", record)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, selects
    assert "language" in selects[0]
    writes = [s for s in statements if s.lstrip().upper().startswith(
        ("INSERT", "UPDATE", "DELETE"))]
    assert writes == []


# ── Anti-enumeration: herkese açık yanıt dil/varlık sızdırmaz ───────────────

_PER_REQUEST_HEADERS = {"X-Request-Id", "X-Request-ID", "Date",
                        "Content-Security-Policy"}


def _wire(response):
    body = response.get_json()
    headers = tuple(sorted(
        (key, value) for key, value in response.headers.items()
        if key not in _PER_REQUEST_HEADERS))
    return response.status_code, json.dumps(body, sort_keys=True), headers


def test_native_forgot_answer_is_identical_for_en_tr_and_unknown(
        app, raw_client, idp, make_user, monkeypatch):
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", True)
    make_user("ingiliz", email="ingiliz@example.com", language="en")
    make_user("turk", email="turk@example.com", language="tr")

    answers = {identifier: _wire(raw_client.post(
        "/api/v1/auth/password/forgot", json={"identifier": identifier}))
        for identifier in ("ingiliz", "turk", "hayalet",
                           "ingiliz@example.com", "hayalet@example.com")}

    assert len(set(answers.values())) == 1, answers
    status, body, _ = answers["ingiliz"]
    assert status == 202
    assert json.loads(body) == {"password_reset": {"status": "accepted"}}
    assert "en" not in body and "language" not in body
    metadata = [kwargs.get("ClientMetadata") for name, kwargs in idp.calls]
    assert metadata == [{"language": "en"}, {"language": "tr"}, None,
                        {"language": "en"}, None]


def test_web_forgot_answer_is_identical_for_en_and_unknown(
        app, client, idp, make_user):
    make_user("webingiliz", email="webingiliz@example.com", language="en")
    known = client.post("/forgot-password", json={"identifier": "webingiliz"})
    unknown = client.post("/forgot-password", json={"identifier": "webhayalet"})
    assert known.status_code == unknown.status_code == 200
    assert known.get_json() == unknown.get_json()
    assert "language" not in known.get_data(as_text=True)


def test_request_body_language_is_not_a_channel(
        app, raw_client, idp, make_user, monkeypatch):
    """Yeni API alanı YOK: gövdedeki `language` e-posta dilini SEÇEMEZ."""
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", True)
    make_user("govde", email="govde@example.com", language="tr")
    response = raw_client.post("/api/v1/auth/password/forgot", json={
        "identifier": "govde", "language": "en"})
    assert response.status_code == 202
    assert idp.only("forgot_password")["ClientMetadata"] == {"language": "tr"}


# ═══════════════════════════════════════════════════════════════════════════
# 4. CustomEmailSender Lambda — tek çözücü
# ═══════════════════════════════════════════════════════════════════════════

def _lambda_handler_module():
    if str(_LAMBDA_SRC) not in sys.path:
        sys.path.insert(0, str(_LAMBDA_SRC))
    import handler
    return handler


@pytest.fixture
def lam(monkeypatch):
    if str(_LAMBDA_SRC) not in sys.path:
        monkeypatch.syspath_prepend(str(_LAMBDA_SRC))
    import email_sender
    import handler

    decrypted = []

    def fake_decrypt(ciphertext):
        decrypted.append(ciphertext)
        return _CODE

    monkeypatch.setattr(handler, "_decrypt_code", fake_decrypt)
    monkeypatch.setattr(email_sender, "RESEND_API_KEY", "re_test_key")
    posts = []
    monkeypatch.setattr(
        email_sender, "_post_json",
        lambda url, payload, headers, timeout=4:
        posts.append({"url": url, "payload": payload, "headers": headers})
        or {"id": "m-1"})
    return {"handler": handler, "posts": posts, "decrypted": decrypted}


_ABSENT = object()


def _event(trigger, metadata=_ABSENT, locale=_ABSENT, name="Ali"):
    attrs = {"email": "ali@example.com"}
    if name is not None:
        attrs["name"] = name
    if locale is not _ABSENT:
        attrs["locale"] = locale
    request = {"type": "customEmailSenderRequestV1", "code": _CIPHERTEXT,
               "userAttributes": attrs}
    if metadata is not _ABSENT:
        request["clientMetadata"] = metadata
    return {"version": "1", "triggerSource": trigger, "userName": "ali",
            "request": request}


def _expected(kind, lang, name="Ali", code=_CODE):
    builder = (email_templates.verification_code_email if kind == "verification"
               else email_templates.reset_code_email)
    return builder(name, code, language=lang)


def _sent(lam):
    assert len(lam["posts"]) == 1, lam["posts"]
    payload = lam["posts"][0]["payload"]
    return payload["subject"], payload["html"], payload["text"]


_KIND = {
    "CustomEmailSender_SignUp": "verification",
    "CustomEmailSender_ResendCode": "verification",
    "CustomEmailSender_VerifyUserAttribute": "verification",
    "CustomEmailSender_UpdateUserAttribute": "verification",
    "CustomEmailSender_ForgotPassword": "reset",
}


# ── SignUp ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
def test_signup_metadata_selects_the_language(lam, lang):
    lam["handler"].handler(_event(
        "CustomEmailSender_SignUp", metadata={"language": lang}, locale=lang), None)
    assert _sent(lam) == _expected("verification", lang)


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_signup_without_metadata_uses_locale(lam, lang):
    lam["handler"].handler(_event("CustomEmailSender_SignUp", locale=lang), None)
    assert _sent(lam) == _expected("verification", lang)


def test_signup_without_any_signal_is_turkish(lam):
    lam["handler"].handler(_event("CustomEmailSender_SignUp"), None)
    assert _sent(lam) == _expected("verification", "tr")


def test_signup_invalid_metadata_and_locale_is_turkish(lam):
    lam["handler"].handler(_event(
        "CustomEmailSender_SignUp", metadata={"language": "de"}, locale="fr"), None)
    assert _sent(lam) == _expected("verification", "tr")


# ── ResendCode (ClientMetadata yok) ─────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
def test_resend_uses_the_signup_locale(lam, lang):
    lam["handler"].handler(_event("CustomEmailSender_ResendCode", locale=lang), None)
    subject, html, text = _sent(lam)
    assert (subject, html, text) == _expected("verification", lang)
    assert html.startswith('<!DOCTYPE html><html lang="%s">' % lang)


def test_resend_for_a_legacy_user_without_locale_is_turkish(lam):
    """Bu PR'dan önce kayıt olmuş kullanıcıda locale yok → kanonik varsayılan;
    çökme yok, backfill yok."""
    event_ = _event("CustomEmailSender_ResendCode")
    assert lam["handler"].handler(event_, None) is event_
    assert _sent(lam) == _expected("verification", "tr")


@pytest.mark.parametrize("bad", _HOSTILE, ids=repr)
def test_resend_malicious_locale_is_turkish(lam, bad):
    lam["handler"].handler(_event("CustomEmailSender_ResendCode", locale=bad), None)
    assert _sent(lam) == _expected("verification", "tr")


# ── ForgotPassword ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
def test_forgot_password_metadata_selects_the_reset_language(lam, lang):
    lam["handler"].handler(_event(
        "CustomEmailSender_ForgotPassword", metadata={"language": lang}), None)
    subject, html, text = _sent(lam)
    assert (subject, html, text) == _expected("reset", lang)


@pytest.mark.parametrize("lang", ["en", "tr"])
def test_forgot_password_invalid_metadata_follows_locale(lam, lang):
    lam["handler"].handler(_event(
        "CustomEmailSender_ForgotPassword", metadata={"language": "xx"},
        locale=lang), None)
    assert _sent(lam) == _expected("reset", lang)


def test_forgot_password_without_any_signal_is_turkish(lam):
    lam["handler"].handler(_event("CustomEmailSender_ForgotPassword"), None)
    assert _sent(lam) == _expected("reset", "tr")


# ── VerifyUserAttribute / UpdateUserAttribute ───────────────────────────────

@pytest.mark.parametrize("trigger", ["CustomEmailSender_VerifyUserAttribute",
                                     "CustomEmailSender_UpdateUserAttribute"])
@pytest.mark.parametrize("locale,expected", [("en", "en"), ("tr", "tr"),
                                             (_ABSENT, "tr"), ("de", "tr")])
def test_attribute_verification_follows_locale_or_default(lam, trigger, locale,
                                                          expected):
    lam["handler"].handler(_event(trigger, locale=locale), None)
    assert _sent(lam) == _expected("verification", expected)


# ── Kesin öncelik (Part 15) ─────────────────────────────────────────────────

@pytest.mark.parametrize("metadata,locale,expected", [
    ({"language": "en"}, "tr", ("en", "metadata")),
    ({"language": "tr"}, "en", ("tr", "metadata")),
    ({"language": "invalid"}, "en", ("en", "locale")),
    ({"language": "invalid"}, "invalid", ("tr", "default")),
    ({}, "en", ("en", "locale")),
    (None, "en", ("en", "locale")),
    ("en", "tr", ("tr", "locale")),          # metadata nesne değil → yok sayılır
    (["en"], None, ("tr", "default")),
    ({"language": "en"}, None, ("en", "metadata")),
    ({"lang": "en"}, None, ("tr", "default")),  # yalnız `language` anahtarı
])
def test_resolver_precedence(metadata, locale, expected):
    handler = _lambda_handler_module()
    request = {} if metadata is None else {"clientMetadata": metadata}
    attrs = {} if locale is None else {"locale": locale}
    assert handler._resolve_language(request, attrs) == expected


@pytest.mark.parametrize("trigger", sorted(_KIND))
@pytest.mark.parametrize("metadata,locale,expected", [
    ({"language": "en"}, "tr", "en"),
    ({"language": "tr"}, "en", "tr"),
    ({"language": "invalid"}, "en", "en"),
    ({"language": "invalid"}, "invalid", "tr"),
])
def test_precedence_holds_for_every_trigger(lam, trigger, metadata, locale,
                                            expected):
    lam["handler"].handler(_event(trigger, metadata=metadata, locale=locale), None)
    assert _sent(lam) == _expected(_KIND[trigger], expected)


# ── Güvenlik: değer yalnızca SEÇER, asla içerik/başlık/yol olmaz ───────────

@pytest.mark.parametrize("bad", _HOSTILE, ids=repr)
@pytest.mark.parametrize("carrier", ["metadata", "locale"])
@pytest.mark.parametrize("trigger", ["CustomEmailSender_SignUp",
                                     "CustomEmailSender_ForgotPassword"])
def test_hostile_language_values_cannot_escape_selection(lam, bad, carrier, trigger):
    kwargs = ({"metadata": {"language": bad}} if carrier == "metadata"
              else {"locale": bad})
    lam["handler"].handler(_event(trigger, **kwargs), None)

    assert _sent(lam) == _expected(_KIND[trigger], "tr")
    post = lam["posts"][0]
    payload = post["payload"]
    # Alıcı, başlık kümesi ve yük anahtarları dil girdisinden bağımsız.
    assert payload["to"] == ["ali@example.com"]
    assert set(payload) == {"from", "to", "subject", "html", "text", "reply_to"}
    assert set(post["headers"]) == {"Authorization"}
    # Kanıt yukarıdaki "tr" çıktısıyla bayt-eşitliktir; bu yalnızca ayırt edici
    # yükleri adlandırır ("de" gibi kısa değerler Türkçe metinde zaten geçer).
    if isinstance(bad, str) and len(bad) > 5:
        for part in (payload["subject"], payload["html"], payload["text"]):
            assert bad not in part
    assert lam["decrypted"] == [_CIPHERTEXT]


def test_hostile_payload_shape_matches_a_clean_send(lam):
    """Dil girdisi gönderim yükünün ŞEKLİNİ değiştiremez."""
    handler = lam["handler"]
    handler.handler(_event("CustomEmailSender_SignUp",
                           metadata={"language": "tr"}), None)
    handler.handler(_event("CustomEmailSender_SignUp",
                           metadata={"language": "en\nBCC: attacker@example.com"},
                           locale="<script>"), None)
    clean, hostile = lam["posts"]
    assert clean == hostile


@pytest.mark.parametrize("metadata", ["en", ["language", "en"], 7, True])
def test_non_object_client_metadata_never_raises(lam, metadata):
    event_ = _event("CustomEmailSender_SignUp", metadata=metadata, locale="en")
    assert lam["handler"].handler(event_, None) is event_
    assert _sent(lam) == _expected("verification", "en")


# ── Kod gizliliği / çözme / escape ──────────────────────────────────────────

@pytest.mark.parametrize("lang", ["en", "tr"])
def test_language_never_changes_the_decrypt_input_or_the_code(lam, lang):
    lam["handler"].handler(_event("CustomEmailSender_ForgotPassword",
                                  metadata={"language": lang}, locale=lang), None)
    assert lam["decrypted"] == [_CIPHERTEXT]
    subject, html, text = _sent(lam)
    assert _CODE in html and _CODE in text and _CODE not in subject


@pytest.mark.parametrize("lang", ["en", "tr"])
@pytest.mark.parametrize("trigger", ["CustomEmailSender_SignUp",
                                     "CustomEmailSender_ResendCode",
                                     "CustomEmailSender_ForgotPassword"])
def test_code_and_name_stay_html_escaped_in_every_language(lam, monkeypatch,
                                                           trigger, lang):
    monkeypatch.setattr(lam["handler"], "_decrypt_code",
                        lambda ciphertext: "<b>1</b>")
    lam["handler"].handler(_event(trigger, metadata={"language": lang},
                                  locale=lang, name="<img src=x>"), None)
    _, html, _ = _sent(lam)
    assert "<b>1</b>" not in html and "&lt;b&gt;1&lt;/b&gt;" in html
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html


def test_greeting_falls_back_to_the_cognito_username(lam):
    lam["handler"].handler(_event("CustomEmailSender_ResendCode", locale="en",
                                  name=None), None)
    assert _sent(lam) == _expected("verification", "en", name="ali")


def test_logs_carry_only_the_allow_listed_language(lam, caplog):
    secret_locale = "en\nBCC: attacker@example.com"
    with caplog.at_level(logging.DEBUG):
        lam["handler"].handler(_event("CustomEmailSender_ResendCode",
                                      locale="en"), None)
        lam["handler"].handler(_event("CustomEmailSender_SignUp",
                                      metadata={"language": secret_locale}), None)
    log = caplog.text
    assert "lang=en lang_source=locale" in log
    assert "lang=tr lang_source=default" in log
    assert _CODE not in log
    assert _CIPHERTEXT not in log
    assert "ali@example.com" not in log
    assert "attacker" not in log and "BCC" not in log
    assert "clientMetadata" not in log and "userAttributes" not in log


# ── Lambda olay-yereldir: DB/VPC/HTTP araması yok ───────────────────────────

def test_lambda_handler_imports_no_database_or_http_client():
    tree = ast.parse((_LAMBDA_SRC / "handler.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"base64", "logging", "os", "email_sender",
                        "email_templates", "aws_encryption_sdk"}, imported


def test_lambda_stack_has_no_vpc_or_database_wiring():
    template = (_LAMBDA_DIR / "template.yaml").read_text(encoding="utf-8")
    for forbidden in ("VpcConfig", "DATABASE_URL", "AWS::RDS", "SecurityGroupIds"):
        assert forbidden not in template


# ═══════════════════════════════════════════════════════════════════════════
# 5. Neden genel User.language → locale senkronu GEREKMEZ
# ═══════════════════════════════════════════════════════════════════════════
#
# ResendCode yalnızca onaylanmamış hesapta e-posta üretir (onaylı hesaba Cognito
# InvalidParameterException döner). Onaylanmamış hesap oturum açamaz; kayıt
# dışındaki her User.language yazıcısı kimliği doğrulanmış bir oturum ister.
# Yani ResendCode anında `locale` (kayıt dili) hâlâ User.language'dir.
# ForgotPassword ise güncel dili metadata ile taşır (yukarıda test edildi).

def _language_writers():
    writers = set()
    for path in sorted((_REPO / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, ast.AugAssign)
                           else [])
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr == "language":
                        writers.add((path.relative_to(_REPO).as_posix(), func.name))
    return writers


def test_every_post_registration_language_writer_requires_an_authenticated_owner():
    assert _language_writers() == {
        ("app/blueprints/auth.py", "set_language"),        # current_user girişli
        ("app/blueprints/auth.py", "_login_fresh"),        # başarılı giriş sonrası
        ("app/blueprints/mobile_account_language.py",
         "put_account_language"),                          # @require_mobile_auth
    }
    source = (_REPO / "app/blueprints/mobile_account_language.py").read_text(
        encoding="utf-8")
    assert "@require_mobile_auth\ndef put_account_language" in source
    auth_source = (_REPO / "app/blueprints/auth.py").read_text(encoding="utf-8")
    body = auth_source[auth_source.index("def set_language"):]
    body = body[:body.index("\ndef ")]
    assert "if current_user.is_authenticated:\n        current_user.language = lang" \
        in body


def test_anonymous_language_choice_never_touches_a_pending_account(
        app, client, idp):
    account_registration.register_account(
        "bekleyen", "bekleyen@example.com", "Sifre1234", language="en")
    response = client.post("/set-language", json={"lang": "tr"})
    assert response.status_code == 200
    assert User.query.filter_by(username="bekleyen").one().language == "en"
    assert _attributes(idp.only("sign_up"))["locale"] == "en"
