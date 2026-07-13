import re
from pathlib import Path

import pytest


ENV_EXAMPLE_SOURCE = (
    Path(__file__).resolve().parents[1] / ".env.example"
).read_text(encoding="utf-8")


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
    assert f"# {setting}" in ENV_EXAMPLE_SOURCE


def test_port_is_not_documented_as_an_environment_setting():
    assert re.search(r"(?m)^\s*#?\s*PORT\s*=", ENV_EXAMPLE_SOURCE) is None


def test_multiple_worker_warning_documents_redis_and_ai_budget_scaling():
    assert (
        "Multiple workers require Redis and multiply per-process AI concurrency budgets."
        in ENV_EXAMPLE_SOURCE
    )
