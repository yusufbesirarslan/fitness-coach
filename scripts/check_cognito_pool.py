#!/usr/bin/env python3
"""Cognito kullanıcı havuzu yapılandırma DRIFT kontrolü (H4).

Havuz konsol-yönetimlidir ve IaC'de DEĞİLDİR (yalnızca Lambda + KMS SAM'de).
Bu, uygulamanın kimlik sağlayıcısı hakkında VARSAYDIĞI güvenlik özelliklerinin
sürümlenmemiş, gözden geçirilmemiş ve sessizce kayabilir olması demek. En
kritiği: app/blueprints/auth.py, UserNotConfirmedException'ın hesap-numaralandırma
sinyalini BİLEREK kabul eder ve gerekçesi "app client'ta PreventUserExistenceErrors
AÇIK" varsayımıdır. O bayrak kapanırsa, kabul edilmiş tradeoff sessizce GERÇEK bir
numaralandırma oracle'ına döner ve hiçbir şey bunu fark etmez.

Kontroller (beklenen değerler koddaki varsayımlardan türetilmiştir):
  PreventUserExistenceErrors = ENABLED   → auth.py'deki numaralandırma tradeoff'u
  MfaConfiguration           = OFF       → authenticate() TÜM challenge'ları reddeder;
                                           MFA açılırsa her giriş kırılır
  ExplicitAuthFlows          ⊇ USER_PASSWORD_AUTH + REFRESH_TOKEN_AUTH
  PasswordPolicy.MinimumLength ≥ 8       → validate_password ile uyumlu
  LambdaConfig.CustomEmailSender set     → yoksa markalı auth e-postaları sessizce
                                           Cognito varsayılanlarına döner

Kullanım:
    python scripts/check_cognito_pool.py \
        --pool-id eu-central-1_kaX0SORRK --client-id 3rdtrk3vl1dp0m1d19gdc3pqib

Çıkış kodları:
    0 = uyumlu VEYA izin yok (AccessDenied → uyarı, deploy bloklanmaz)
    1 = DRIFT saptandı
    2 = kullanım hatası

Deploy: .github/workflows/deploy.yml içinde `continue-on-error: true` ile çalışır.
Deploy rolüne cognito-idp:DescribeUserPool + DescribeUserPoolClient eklendikten
sonra continue-on-error kaldırılıp BLOKLAYICI yapılabilir.
"""
import argparse
import os
import sys

MIN_PASSWORD_LENGTH = 8
REQUIRED_AUTH_FLOWS = ("ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH")


def evaluate(pool, client):
    """Saf değerlendirme — ağ yok. describe-* yanıtlarını alır, sorun listesi döner."""
    problems = []

    prevent = client.get("PreventUserExistenceErrors")
    if prevent != "ENABLED":
        problems.append(
            f"PreventUserExistenceErrors={prevent!r} (beklenen 'ENABLED') — "
            "auth.py'deki hesap-numaralandırma tradeoff'u bu bayrağa DAYANIR; "
            "kapalıyken /login ve /verify gerçek bir numaralandırma oracle'ı olur")

    mfa = pool.get("MfaConfiguration")
    if mfa != "OFF":
        problems.append(
            f"MfaConfiguration={mfa!r} (beklenen 'OFF') — cognito_service.authenticate() "
            "TÜM challenge yanıtlarını reddeder; MFA açıkken HİÇBİR kullanıcı giriş yapamaz")

    flows = client.get("ExplicitAuthFlows") or []
    for required in REQUIRED_AUTH_FLOWS:
        if required not in flows:
            problems.append(
                f"ExplicitAuthFlows içinde {required} YOK (mevcut: {sorted(flows)}) — "
                "native backend akışı bu flow'u kullanır")

    min_len = ((pool.get("Policies") or {}).get("PasswordPolicy") or {}).get(
        "MinimumLength")
    if not isinstance(min_len, int) or min_len < MIN_PASSWORD_LENGTH:
        problems.append(
            f"PasswordPolicy.MinimumLength={min_len!r} (beklenen >= {MIN_PASSWORD_LENGTH}) — "
            "app/services/validators.py validate_password ile uyumsuz")

    if not (pool.get("LambdaConfig") or {}).get("CustomEmailSender"):
        problems.append(
            "LambdaConfig.CustomEmailSender BAĞLI DEĞİL — doğrulama/sıfırlama "
            "e-postaları markasız Cognito varsayılanlarına döner "
            "(bkz. infra/cognito-email-sender/README.md runbook)")

    return problems


def mobile_posture(client):
    """Return mobile-relevant app-client posture without returning secrets."""
    units = client.get("TokenValidityUnits") or {}

    def lifetime(field, unit_field, fallback):
        value = client.get(field)
        unit = units.get(unit_field, fallback)
        return f"{value} {unit}" if value is not None else "not reported"

    return {
        "client_secret_present": bool(client.get("ClientSecret")),
        "token_revocation_enabled": bool(client.get("EnableTokenRevocation")),
        "access_token_lifetime": lifetime(
            "AccessTokenValidity", "AccessToken", "hours"),
        "id_token_lifetime": lifetime(
            "IdTokenValidity", "IdToken", "hours"),
        "refresh_token_lifetime": lifetime(
            "RefreshTokenValidity", "RefreshToken", "days"),
        "refresh_token_rotation": (
            (client.get("RefreshTokenRotation") or {}).get("Feature")
            or "not reported"),
    }


def _describe(pool_id, client_id):
    import boto3
    idp = boto3.client("cognito-idp")
    pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    client = idp.describe_user_pool_client(
        UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]
    return pool, client


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-id", default=os.environ.get("COGNITO_USER_POOL_ID"))
    parser.add_argument("--client-id", default=os.environ.get("COGNITO_APP_CLIENT_ID"))
    args = parser.parse_args(argv)

    if not args.pool_id or not args.client_id:
        print("HATA: --pool-id ve --client-id (veya COGNITO_* env) gerekli",
              file=sys.stderr)
        return 2

    try:
        pool, client = _describe(args.pool_id, args.client_id)
    except Exception as exc:  # noqa: BLE001 — izin/ağ hatası deploy'u BLOKLAMAZ
        code = ""
        try:
            code = exc.response["Error"]["Code"]
        except Exception:
            pass
        if code in ("AccessDenied", "AccessDeniedException",
                    "UnrecognizedClientException", "CredentialsError"):
            print("UYARI: Cognito havuz yapılandırması DOĞRULANAMADI — deploy rolünde "
                  "cognito-idp:DescribeUserPool / DescribeUserPoolClient izni yok. "
                  "İzni ekleyip bu adımı bloklayıcı yapın (H4).")
            return 0
        print(f"UYARI: havuz yapılandırması okunamadı "
              f"({type(exc).__name__}: {code or 'unclassified'}) "
              "— deploy bloklanmadı.")
        return 0

    problems = evaluate(pool, client)
    posture = mobile_posture(client)
    print("Mobile app-client posture: " + ", ".join(
        f"{name}={value}" for name, value in posture.items()))
    if not problems:
        print("Cognito havuz yapılandırması UYUMLU "
              "(PreventUserExistenceErrors, MFA, auth flows, parola politikası, "
              "CustomEmailSender).")
        return 0

    print("HAVUZ YAPILANDIRMA DRIFT'İ SAPTANDI:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
