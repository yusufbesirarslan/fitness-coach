"""scripts/check_email_lambda.py — CustomEmailSender Lambda dağıtım kontrolü.

2026-07-13'te `sam build` atlanarak deploy edilen Lambda bağımlılıkları vendor
etmedi; handler istisnaları YUTTUĞU için Cognito trigger'ı başarılı saydı ve auth
e-postaları SESSİZCE hiç gitmedi. Bu testler değerlendirme mantığını sabitler —
ağ/AWS yok.

    python -m pytest tests/test_check_email_lambda.py -v
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_email_lambda", Path("scripts/check_email_lambda.py"))
check_email_lambda = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_email_lambda)


def _good_config(**over):
    config = {
        "MemorySize": 512,
        "Timeout": 20,
        "Environment": {"Variables": {"RESEND_API_KEY": "re_live_key"}},
    }
    config.update(over)
    return config


def _good_entries(*drop):
    entries = {"aws_encryption_sdk", "cryptography",
               "handler.py", "email_sender.py", "email_templates.py"}
    return entries - set(drop)


def test_compliant_deployment_has_no_problems():
    assert check_email_lambda.evaluate(_good_config(), _good_entries()) == []


def test_the_2026_07_13_outage_is_caught():
    """REGRESYON: `sam deploy` (build'siz) → bağımlılıklar yok, bellek/timeout düşük,
    RESEND_API_KEY boş. Tam olarak bu kombinasyon kayıt akışını sessizce öldürdü."""
    problems = check_email_lambda.evaluate(
        {"MemorySize": 256, "Timeout": 5,
         "Environment": {"Variables": {"RESEND_API_KEY": ""}}},
        {"handler.py", "email_sender.py", "email_templates.py"})
    assert any("aws_encryption_sdk" in p for p in problems)
    assert any("MemorySize" in p for p in problems)
    assert any("RESEND_API_KEY" in p for p in problems)


@pytest.mark.parametrize("package", ["aws_encryption_sdk", "cryptography"])
def test_unvendored_dependency_is_flagged(package):
    """handler._decrypt_code bu iki paketi import eder; yoksa her invoke düşer."""
    problems = check_email_lambda.evaluate(_good_config(), _good_entries(package))
    assert any(package in p for p in problems)


@pytest.mark.parametrize("module", ["handler.py", "email_sender.py", "email_templates.py"])
def test_missing_handler_module_is_flagged(module):
    problems = check_email_lambda.evaluate(_good_config(), _good_entries(module))
    assert any(module in p for p in problems)


@pytest.mark.parametrize("memory", [128, 256, 511])
def test_low_memory_is_flagged(memory):
    """Düşük bellekte aws_encryption_sdk importu INIT timeout'a girer → sonsuz timeout."""
    problems = check_email_lambda.evaluate(
        _good_config(MemorySize=memory), _good_entries())
    assert any("MemorySize" in p for p in problems)


@pytest.mark.parametrize("timeout", [3, 5, 19])
def test_low_timeout_is_flagged(timeout):
    problems = check_email_lambda.evaluate(
        _good_config(Timeout=timeout), _good_entries())
    assert any("Timeout" in p for p in problems)


@pytest.mark.parametrize("key", ["", "   ", None])
def test_missing_resend_api_key_is_flagged(key):
    """`sam deploy` --parameter-overrides olmadan → default '' → SESSİZ no-op."""
    problems = check_email_lambda.evaluate(
        _good_config(Environment={"Variables": {"RESEND_API_KEY": key}}),
        _good_entries())
    assert any("RESEND_API_KEY" in p for p in problems)


def test_absent_environment_block_is_flagged():
    problems = check_email_lambda.evaluate(
        _good_config(Environment={}), _good_entries())
    assert any("RESEND_API_KEY" in p for p in problems)
