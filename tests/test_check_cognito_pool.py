"""scripts/check_cognito_pool.py — havuz yapılandırma drift kontrolü (H4).

Havuz IaC'de olmadığı için, uygulamanın kimlik sağlayıcısı hakkındaki
VARSAYIMLARI (PreventUserExistenceErrors açık, MFA kapalı, doğru auth flow'lar)
sürümlenmemiş konsol ayarlarında yaşar. Bu testler değerlendirme mantığını
sabitler — ağ/AWS yok.

    python -m pytest tests/test_check_cognito_pool.py -v
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_cognito_pool", Path("scripts/check_cognito_pool.py"))
check_cognito_pool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_cognito_pool)


def _good_pool(**over):
    pool = {
        "MfaConfiguration": "OFF",
        "Policies": {"PasswordPolicy": {"MinimumLength": 8}},
        "LambdaConfig": {"CustomEmailSender": {
            "LambdaArn": "arn:aws:lambda:eu-central-1:1:function:sender",
            "LambdaVersion": "V1_0"}},
    }
    pool.update(over)
    return pool


def _good_client(**over):
    client = {
        "PreventUserExistenceErrors": "ENABLED",
        "ExplicitAuthFlows": ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    }
    client.update(over)
    return client


def test_compliant_pool_has_no_problems():
    assert check_cognito_pool.evaluate(_good_pool(), _good_client()) == []


def test_prevent_user_existence_errors_disabled_is_flagged():
    """auth.py'deki numaralandırma tradeoff'unun TEK dayanağı bu bayrak."""
    problems = check_cognito_pool.evaluate(
        _good_pool(), _good_client(PreventUserExistenceErrors="LEGACY"))
    assert len(problems) == 1
    assert "PreventUserExistenceErrors" in problems[0]


def test_missing_prevent_user_existence_errors_is_flagged():
    client = _good_client()
    del client["PreventUserExistenceErrors"]
    assert any("PreventUserExistenceErrors" in p
               for p in check_cognito_pool.evaluate(_good_pool(), client))


@pytest.mark.parametrize("mfa", ["ON", "OPTIONAL"])
def test_mfa_enabled_is_flagged(mfa):
    """MFA açıksa authenticate() TÜM challenge'ları reddeder → her giriş kırılır."""
    problems = check_cognito_pool.evaluate(
        _good_pool(MfaConfiguration=mfa), _good_client())
    assert any("MfaConfiguration" in p for p in problems)


@pytest.mark.parametrize("missing", [
    "ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"])
def test_missing_auth_flow_is_flagged(missing):
    flows = [f for f in ("ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH")
             if f != missing]
    problems = check_cognito_pool.evaluate(
        _good_pool(), _good_client(ExplicitAuthFlows=flows))
    assert any(missing in p for p in problems)


def test_weak_password_policy_is_flagged():
    problems = check_cognito_pool.evaluate(
        _good_pool(Policies={"PasswordPolicy": {"MinimumLength": 6}}), _good_client())
    assert any("MinimumLength" in p for p in problems)


def test_unbound_custom_email_sender_is_flagged():
    """Trigger bağlı değilse markalı auth e-postaları sessizce kaybolur."""
    problems = check_cognito_pool.evaluate(
        _good_pool(LambdaConfig={}), _good_client())
    assert any("CustomEmailSender" in p for p in problems)


def test_multiple_drifts_all_reported():
    problems = check_cognito_pool.evaluate(
        _good_pool(MfaConfiguration="ON", LambdaConfig={}),
        _good_client(PreventUserExistenceErrors="LEGACY"))
    assert len(problems) == 3


def test_access_denied_does_not_block_deploy(monkeypatch, capsys):
    """İzin yoksa deploy BLOKLANMAZ — sadece yüksek sesle uyarılır (H4 kararı)."""
    class _Denied(Exception):
        response = {"Error": {"Code": "AccessDenied"}}

    def boom(pool_id, client_id):
        raise _Denied()

    monkeypatch.setattr(check_cognito_pool, "_describe", boom)
    rc = check_cognito_pool.main(["--pool-id", "p", "--client-id", "c"])
    assert rc == 0
    assert "UYARI" in capsys.readouterr().out


def test_drift_returns_nonzero(monkeypatch):
    monkeypatch.setattr(
        check_cognito_pool, "_describe",
        lambda p, c: (_good_pool(MfaConfiguration="ON"), _good_client()))
    assert check_cognito_pool.main(["--pool-id", "p", "--client-id", "c"]) == 1


def test_compliant_returns_zero(monkeypatch):
    monkeypatch.setattr(
        check_cognito_pool, "_describe", lambda p, c: (_good_pool(), _good_client()))
    assert check_cognito_pool.main(["--pool-id", "p", "--client-id", "c"]) == 0


def test_missing_args_is_usage_error(monkeypatch):
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_APP_CLIENT_ID", raising=False)
    assert check_cognito_pool.main([]) == 2


def test_mobile_posture_contains_no_secret_value():
    client = _good_client(
        ClientSecret="raw-client-secret",
        EnableTokenRevocation=True,
        AccessTokenValidity=15,
        IdTokenValidity=15,
        RefreshTokenValidity=7,
        TokenValidityUnits={
            "AccessToken": "minutes", "IdToken": "minutes",
            "RefreshToken": "days"},
        RefreshTokenRotation={"Feature": "DISABLED"},
    )
    posture = check_cognito_pool.mobile_posture(client)
    assert posture == {
        "client_secret_present": True,
        "token_revocation_enabled": True,
        "access_token_lifetime": "15 minutes",
        "id_token_lifetime": "15 minutes",
        "refresh_token_lifetime": "7 days",
        "refresh_token_rotation": "DISABLED",
    }
    assert "raw-client-secret" not in repr(posture)


def test_unexpected_provider_error_does_not_expose_raw_text(monkeypatch, capsys):
    monkeypatch.setattr(
        check_cognito_pool, "_describe",
        lambda pool_id, client_id: (_ for _ in ()).throw(
            RuntimeError("raw-provider-secret-payload")))
    assert check_cognito_pool.main(["--pool-id", "p", "--client-id", "c"]) == 0
    output = capsys.readouterr().out
    assert "raw-provider-secret-payload" not in output
    assert "RuntimeError" in output
