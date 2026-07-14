#!/usr/bin/env python3
"""CustomEmailSender Lambda DAĞITIM kontrolü — "sessizce e-posta göndermeyen Lambda".

Neden var: 2026-07-13'te kayıt akışı TAMAMEN kırıldı ve HİÇBİR alarm çalmadı.
`sam build` çalıştırılmadan `sam deploy` yapıldı → paket bağımlılıkları VENDOR
ETMEDİ → her invoke `ModuleNotFoundError: No module named 'aws_encryption_sdk'`
ile düştü. handler.py sözleşme gereği istisnaları YUTAR (auth, e-posta yüzünden
asla başarısız olmaz), dolayısıyla Cognito trigger'ı BAŞARILI saydı: /register
200 "doğrulama e-postası gönderildi" döndü, e-posta ise HİÇ gitmedi. Kullanıcı
UNCONFIRMED'da mahsur kaldı; yeniden kayıt UsernameExists ile reddedildi.

README zaten `sam build && sam deploy` diyor — eksik olan DOKÜMAN değil, bunu
DOĞRULAYAN bir kontroldü. (README'nin kendisi Windows'ta wheel sorununu da
uyarır: `sam build` sessizce eksik paket üretebilir.) Bu script deploy edilmiş
ARTIFACT'ı denetler, niyeti değil.

Kontroller (hepsi gerçekten yaşanmış arızalardan türetilmiştir):
  Paket aws_encryption_sdk/ + cryptography/ içerir → 2026-07-13 arızası
  MemorySize >= 512 MB, Timeout >= 20 sn        → 256MB/5s'te INIT timeout'a
                                                   girip her invoke'a import'u geri
                                                   iten sonsuz-timeout tuzağı
  RESEND_API_KEY dolu                            → `sam deploy` --parameter-overrides
                                                   olmadan default '' = SESSİZ no-op

Kullanım:
    python scripts/check_email_lambda.py --function-name <ad veya ARN>

Çıkış kodları:
    0 = uyumlu VEYA izin yok (AccessDenied → uyarı, deploy bloklanmaz)
    1 = ARIZA saptandı (auth e-postaları gitmiyor olabilir)
    2 = kullanım hatası

Deploy: .github/workflows/deploy.yml içinde check_cognito_pool.py ile aynı
desende çalışır. Deploy rolüne lambda:GetFunction eklendikten sonra
continue-on-error kaldırılıp BLOKLAYICI yapılabilir.
"""
import argparse
import io
import os
import sys
import urllib.request
import zipfile

MIN_MEMORY_MB = 512
MIN_TIMEOUT_SECONDS = 20
REQUIRED_PACKAGES = ("aws_encryption_sdk", "cryptography")
REQUIRED_MODULES = ("handler.py", "email_sender.py", "email_templates.py")


def evaluate(config, entries):
    """Saf değerlendirme — ağ yok.

    config: get_function_configuration yanıtı.
    entries: deploy edilmiş zip'teki ÜST DÜZEY girdi adları (set).
    """
    problems = []

    for package in REQUIRED_PACKAGES:
        if package not in entries:
            problems.append(
                f"Paket '{package}/' VENDOR EDİLMEMİŞ — handler._decrypt_code import'ta "
                "ModuleNotFoundError ile düşer, istisna YUTULUR ve auth e-postaları "
                "SESSİZCE hiç gitmez (2026-07-13 arızası). Neden: `sam build` "
                "çalıştırılmadan `sam deploy` yapıldı. Düzeltme: "
                "cd infra/cognito-email-sender && sam build && sam deploy "
                "--parameter-overrides ResendApiKey=<key>")

    for module in REQUIRED_MODULES:
        if module not in entries:
            problems.append(f"Handler modülü '{module}' pakette YOK — CodeUri/build bozuk")

    memory = config.get("MemorySize")
    if not isinstance(memory, int) or memory < MIN_MEMORY_MB:
        problems.append(
            f"MemorySize={memory!r} MB (beklenen >= {MIN_MEMORY_MB}) — aws_encryption_sdk + "
            "cryptography import'u düşük bellekte INIT timeout'a girer; timeout sonrası "
            "'suppressed init' importu her invoke'a geri iter → sonsuz timeout, e-posta HİÇ çıkmaz")

    timeout = config.get("Timeout")
    if not isinstance(timeout, int) or timeout < MIN_TIMEOUT_SECONDS:
        problems.append(
            f"Timeout={timeout!r} sn (beklenen >= {MIN_TIMEOUT_SECONDS}) — bkz. MemorySize notu")

    env = (config.get("Environment") or {}).get("Variables") or {}
    if not (env.get("RESEND_API_KEY") or "").strip():
        problems.append(
            "RESEND_API_KEY BOŞ — email_sender 'servis kapalı' moduna düşer ve auth "
            "e-postaları SESSİZCE hiç gitmez. Neden: `sam deploy` --parameter-overrides "
            "ResendApiKey=<key> olmadan çalıştırıldı (default '')")

    return problems


def _fetch(function_name):
    """Deploy EDİLMİŞ artifact'ı indir ve üst düzey girdilerini çıkar."""
    import boto3
    lam = boto3.client("lambda")
    fn = lam.get_function(FunctionName=function_name)
    config = fn["Configuration"]
    with urllib.request.urlopen(fn["Code"]["Location"]) as resp:  # noqa: S310 — AWS imzalı URL
        blob = resp.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        entries = {name.split("/")[0] for name in zf.namelist()}
    return config, entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-name",
                        default=os.environ.get("COGNITO_EMAIL_LAMBDA_NAME"))
    args = parser.parse_args(argv)

    if not args.function_name:
        print("HATA: --function-name (veya COGNITO_EMAIL_LAMBDA_NAME env) gerekli",
              file=sys.stderr)
        return 2

    try:
        config, entries = _fetch(args.function_name)
    except Exception as exc:  # noqa: BLE001 — izin/ağ hatası deploy'u BLOKLAMAZ
        code = ""
        try:
            code = exc.response["Error"]["Code"]
        except Exception:
            pass
        if code in ("AccessDenied", "AccessDeniedException",
                    "UnrecognizedClientException", "CredentialsError"):
            print("UYARI: CustomEmailSender Lambda DOĞRULANAMADI — deploy rolünde "
                  "lambda:GetFunction izni yok. İzni ekleyip bu adımı bloklayıcı yapın.")
            return 0
        print(f"UYARI: Lambda okunamadı ({type(exc).__name__}: {code or exc}) "
              "— deploy bloklanmadı.")
        return 0

    problems = evaluate(config, entries)
    if not problems:
        print("CustomEmailSender Lambda UYUMLU (bağımlılıklar vendor edilmiş, "
              "bellek/timeout yeterli, RESEND_API_KEY dolu).")
        return 0

    print("CustomEmailSender Lambda ARIZASI SAPTANDI "
          "(auth e-postaları SESSİZCE gitmiyor olabilir):")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
