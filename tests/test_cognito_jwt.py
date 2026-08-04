"""cognito_jwt JWKS doğrulayıcı testleri — yerel RSA anahtar çifti + sahte JWKS.
Ağ yok, gerçek Cognito yok: imza/issuer/audience/exp/token_use dallarını sabitler.

    python -m pytest tests/test_cognito_jwt.py -v
"""
import time
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt as jose_jwt
from joserfc.jwk import KeySet, RSAKey

from app.services import cognito_jwt
from app.services.cognito_jwt import TokenValidationError


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signer(rsa_key, monkeypatch):
    """Yerel anahtarın public tarafını sahte JWKS olarak enjekte et; token üretici döndür."""
    priv = RSAKey.import_key(
        rsa_key, parameters={"kid": "test-kid", "use": "sig", "alg": "RS256"})
    keyset = KeySet.import_key_set({"keys": [priv.as_dict(private=False)]})
    monkeypatch.setattr(cognito_jwt, "_load_jwks", lambda force=False: keyset)

    def _make(claims):
        header = {"alg": "RS256", "kid": "test-kid"}
        return jose_jwt.encode(header, claims, priv, algorithms=["RS256"])

    return _make


def _claims(use="access", **over):
    now = int(time.time())
    base = {
        "iss": cognito_jwt._ISSUER,
        "sub": "sub-123",
        "token_use": use,
        "exp": now + 3600,
        "iat": now,
    }
    if use == "id":
        base["aud"] = "client-123"
    else:
        base["client_id"] = "client-123"
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    monkeypatch.setattr(cognito_jwt, "COGNITO_APP_CLIENT_ID", "client-123")
    # Zorunlu JWKS tazeleme soğuma penceresi SÜREÇ-GENELİ modül durumudur
    # (Hardening PR4). Sıfırlanmazsa bir testin tetiklediği tazeleme sonraki
    # testin tazelemesini bastırır ve testler sıraya bağımlı hâle gelirdi.
    cognito_jwt._reset_cache()


def test_valid_access_token(signer):
    claims = cognito_jwt.validate_token(signer(_claims("access")), "access")
    assert claims["sub"] == "sub-123"


def test_valid_id_token(signer):
    claims = cognito_jwt.validate_token(signer(_claims("id")), "id")
    assert claims["aud"] == "client-123"


def test_expired_token_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access", exp=int(time.time()) - 10)), "access")
    assert e.value.reason == "expired"


def test_token_without_exp_rejected(signer):
    """exp ZORUNLU: süresiz bir token asla kabul edilmemeli."""
    claims = _claims("access")
    claims.pop("exp")
    with pytest.raises(TokenValidationError):
        cognito_jwt.validate_token(signer(claims), "access")


def test_modified_signature_rejected(signer):
    tok = signer(_claims("access"))
    tampered = tok[:-3] + ("aaa" if tok[-3:] != "aaa" else "bbb")
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(tampered, "access")
    assert e.value.reason == "invalid_signature"


def test_cached_matching_kid_bad_signature_does_not_fetch(rsa_key, monkeypatch):
    trusted = RSAKey.import_key(
        rsa_key, parameters={"kid": "shared-kid", "use": "sig", "alg": "RS256"})
    attacker = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "shared-kid", "use": "sig", "alg": "RS256"})
    cognito_jwt._jwks_cache = KeySet.import_key_set({
        "keys": [trusted.as_dict(private=False)]})
    token = jose_jwt.encode(
        {"alg": "RS256", "kid": "shared-kid"}, _claims("access"), attacker,
        algorithms=["RS256"])
    fetches = {"n": 0}

    def fail_fetch(*args, **kwargs):
        fetches["n"] += 1
        raise OSError("offline")

    monkeypatch.setattr(cognito_jwt.urllib.request, "urlopen", fail_fetch)

    with pytest.raises(TokenValidationError) as exc:
        cognito_jwt.validate_token(token, "access")
    assert exc.value.reason == "invalid_signature"
    assert fetches["n"] == 0


def test_unknown_kid_fetch_failure_is_temporary(rsa_key, monkeypatch):
    old = RSAKey.import_key(
        rsa_key, parameters={"kid": "old-kid", "use": "sig", "alg": "RS256"})
    rotated = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "new-kid", "use": "sig", "alg": "RS256"})
    cognito_jwt._jwks_cache = KeySet.import_key_set({
        "keys": [old.as_dict(private=False)]})
    token = jose_jwt.encode(
        {"alg": "RS256", "kid": "new-kid"}, _claims("access"), rotated,
        algorithms=["RS256"])
    monkeypatch.setattr(
        cognito_jwt.urllib.request, "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))

    with pytest.raises(TokenValidationError) as exc:
        cognito_jwt.validate_token(token, "access")
    assert exc.value.reason == "jwks_unavailable"


def test_expiry_leeway_is_configurable(signer):
    token = signer(_claims("access", exp=int(time.time()) - 2))
    claims = cognito_jwt.validate_token(token, "access", leeway_seconds=5)
    assert claims["sub"] == "sub-123"


def test_wrong_token_use_rejected(signer):
    # access token doğrulanırken "id" beklenirse reddet.
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access")), "id")
    assert e.value.reason in ("wrong_use", "wrong_audience")


def test_wrong_audience_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("id", aud="someone-else")), "id")
    assert e.value.reason == "wrong_audience"


def test_wrong_issuer_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access", iss="https://evil.example")), "access")
    assert e.value.reason == "wrong_issuer"


def test_malformed_token_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token("not-a-jwt", "access")
    assert e.value.reason in ("malformed", "invalid_signature")


def test_unknown_kid_triggers_single_refetch(signer, rsa_key, monkeypatch):
    """Anahtar rotasyonu: bilinmeyen kid → JWKS bir kez YENİDEN çekilir ve token
    yeni anahtar setiyle doğrulanır (kullanıcı rotasyonda dışarı atılmaz)."""
    rotated = RSAKey.import_key(
        rsa_key, parameters={"kid": "new-kid", "use": "sig", "alg": "RS256"})
    token = jose_jwt.encode(
        {"alg": "RS256", "kid": "new-kid"}, _claims("access"), rotated,
        algorithms=["RS256"])

    stale = KeySet.import_key_set({"keys": [RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "old-kid", "use": "sig", "alg": "RS256"},
    ).as_dict(private=False)]})
    fresh = KeySet.import_key_set({"keys": [rotated.as_dict(private=False)]})

    calls = {"n": 0, "forced": 0}

    def fake_load_jwks(force=False):
        calls["n"] += 1
        if force:
            calls["forced"] += 1
        return fresh if force else stale

    monkeypatch.setattr(cognito_jwt, "_load_jwks", fake_load_jwks)

    claims = cognito_jwt.validate_token(token, "access")
    assert claims["sub"] == "sub-123"
    # AĞ'a çıkan tek çağrı zorunlu tazelemedir; zorlamasız çağrılar sıcak
    # önbellekte saf bellek okumasıdır (PR4 tek-uçuş çift-kontrolü bir tane
    # daha ekler — kilidi bekleyen thread'in ağ'a çıkmadan önceki son bakışı).
    assert calls["forced"] == 1
    assert calls["n"] == 3


def test_matching_kid_signature_failure_is_definitive(monkeypatch):
    """İlk keyset imzayı doğrulayamaz (JoseError) → yeniden çekme tetiklenir. Yeniden
    çekme (force=True) alt yapı hatasıyla başarısız olursa gerçek neden
    (jwks_unavailable) invalid_signature ile ezilmemeli.

    Bu ayrım H1'in temelidir: require_auth jwks_unavailable'ı GEÇİCİ sayıp 503
    döner ve oturumu SİLMEZ; invalid_signature ise KESİN rettir."""
    # Token "shared-kid" ile imzalanır; ilk _load_jwks çağrısı (force=False) AYNI kid'i
    # taşıyan ama FARKLI bir anahtarın public tarafını döner → kid bulunur, imza
    # doğrulaması joserfc.errors.BadSignatureError (bir JoseError alt sınıfı)
    # fırlatır ve retry dalını tetikler.
    real_key = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "shared-kid", "use": "sig", "alg": "RS256"})
    token = jose_jwt.encode(
        {"alg": "RS256", "kid": "shared-kid"}, _claims("access"), real_key,
        algorithms=["RS256"])

    other_key = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "shared-kid", "use": "sig", "alg": "RS256"})
    stale_keyset = KeySet.import_key_set({"keys": [other_key.as_dict(private=False)]})

    calls = {"n": 0}

    def fake_load_jwks(force=False):
        calls["n"] += 1
        if not force:
            return stale_keyset
        raise TokenValidationError("jwks_unavailable")

    monkeypatch.setattr(cognito_jwt, "_load_jwks", fake_load_jwks)

    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(token, "access")
    assert e.value.reason == "invalid_signature"
    assert calls["n"] == 1
