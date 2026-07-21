import re
from pathlib import Path

import pytest


ENV_EXAMPLE_SOURCE = (
    Path(__file__).resolve().parents[1] / ".env.example"
).read_text(encoding="utf-8")
RUNTIME_HEADING = "# ── Çalışma zamanı / loglama / Gunicorn ──"
WORKER_WARNING = (
    "# UYARI: Birden fazla web worker kullanmak Redis gerektirir ve süreç başına "
    "AI eşzamanlılık bütçelerini worker sayısıyla çarpar."
)


def _has_exact_commented_setting(source, setting):
    return f"# {setting}" in source.splitlines()


def _contains_port_setting(source):
    return (
        re.search(r"(?m)^\s*#?\s*(?:export\s+)?PORT\s*=", source) is not None
    )


@pytest.mark.parametrize(
    "setting",
    [
        "FLASK_ENV=development",
        "LOG_LEVEL=INFO",
        "FITX_WEB_WORKERS=1",
        "FITX_WEB_THREADS=8",
    ],
)
def test_runtime_setting_is_documented_as_a_commented_example(setting):
    assert _has_exact_commented_setting(ENV_EXAMPLE_SOURCE, setting)


@pytest.mark.parametrize(
    "source",
    [
        "prefix # FLASK_ENV=development",
        "# FLASK_ENV=development-extra",
        "# FLASK_ENV=development # note",
    ],
)
def test_commented_setting_matcher_rejects_partial_lines(source):
    assert not _has_exact_commented_setting(source, "FLASK_ENV=development")


def test_port_is_not_documented_as_an_environment_setting():
    assert not _contains_port_setting(ENV_EXAMPLE_SOURCE)


@pytest.mark.parametrize(
    "source",
    [
        "PORT=5000",
        "# PORT=5000",
        "export PORT=5000",
        "# export PORT=5000",
    ],
)
def test_port_setting_matcher_detects_supported_dotenv_forms(source):
    assert _contains_port_setting(source)


def test_runtime_settings_are_grouped_after_local_development_settings():
    lines = ENV_EXAMPLE_SOURCE.splitlines()
    expected_order = [
        "# Yalnızca yerel geliştirme için (prod'da KAPALI bırak):",
        "# FLASK_DEBUG=1",
        "# FLASK_ENV=development",
        RUNTIME_HEADING,
        "# LOG_LEVEL=INFO",
        "# FITX_WEB_WORKERS=1",
        "# FITX_WEB_THREADS=8",
    ]

    for line in expected_order:
        assert line in lines
    assert [lines.index(line) for line in expected_order] == sorted(
        lines.index(line) for line in expected_order
    )


def test_multiple_worker_warning_documents_redis_and_ai_budget_scaling():
    assert WORKER_WARNING in ENV_EXAMPLE_SOURCE.splitlines()


def test_coach_turn_timeout_is_documented():
    assert _has_exact_commented_setting(
        ENV_EXAMPLE_SOURCE, "AI_COACH_TURN_TIMEOUT_SECONDS=90"
    )


def test_adaptive_plan_context_flag_is_documented_default_off():
    example = Path(".env.example").read_text(encoding="utf-8")
    assert "# AI_ADAPTIVE_PLAN_CONTEXT=0" in example
    assert "AI_ADAPTIVE_PLAN_CONTEXT=1" not in example
