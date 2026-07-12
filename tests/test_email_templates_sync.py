"""Şablon eşitlik nöbetçisi — tek kaynak, iki çalışma zamanı.

app/services/email_templates.py hem Flask'ta hem Cognito CustomEmailSender
Lambda'sında (infra/cognito-email-sender/src/) çalışır. Symlink/derleme sihri
yerine commit'lenmiş bir kopya kullanılır; bu test iki dosyanın bayt-bayt aynı
kalmasını zorlar. Şablonu değiştirdiysen kopyayı güncelle:

    cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_APP_COPY = _REPO / "app" / "services" / "email_templates.py"
_LAMBDA_COPY = _REPO / "infra" / "cognito-email-sender" / "src" / "email_templates.py"


def test_lambda_template_copy_is_byte_identical():
    assert _LAMBDA_COPY.exists(), (
        "Lambda şablon kopyası eksik — kopyala: "
        "cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py")
    assert _APP_COPY.read_bytes() == _LAMBDA_COPY.read_bytes(), (
        "app/services/email_templates.py ile Lambda kopyası uyuşmuyor. "
        "Şablon değişikliğinden sonra kopyayı güncelle: "
        "cp app/services/email_templates.py infra/cognito-email-sender/src/email_templates.py")
