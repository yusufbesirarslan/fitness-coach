import runpy
from pathlib import Path

import pytest


CONFIG = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"


def _load_config(monkeypatch, **environment):
    monkeypatch.delenv("FITX_WEB_WORKERS", raising=False)
    monkeypatch.delenv("FITX_WEB_THREADS", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return runpy.run_path(str(CONFIG))


def test_gunicorn_config_uses_safe_worker_and_thread_defaults(monkeypatch):
    config = _load_config(monkeypatch)

    assert config["workers"] == 1
    assert config["threads"] == 8


def test_gunicorn_config_accepts_positive_worker_and_thread_overrides(monkeypatch):
    config = _load_config(
        monkeypatch,
        FITX_WEB_WORKERS="3",
        FITX_WEB_THREADS="6",
    )

    assert config["workers"] == 3
    assert config["threads"] == 6


def test_gunicorn_uses_application_request_logs_and_keeps_error_log(monkeypatch):
    config = _load_config(monkeypatch)

    assert config["accesslog"] is None
    assert config["errorlog"] == "-"


@pytest.mark.parametrize("value", ["0", "invalid"])
def test_gunicorn_config_falls_back_for_non_positive_or_invalid_values(
    monkeypatch, value
):
    config = _load_config(
        monkeypatch,
        FITX_WEB_WORKERS=value,
        FITX_WEB_THREADS=value,
    )

    assert config["workers"] == 1
    assert config["threads"] == 8
